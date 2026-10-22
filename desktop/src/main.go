package main

import (
	"context"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"os/exec"
	"runtime"
	"sync"
	"time"

	"github.com/wailsapp/wails/v3/pkg/application"
	"github.com/wailsapp/wails/v3/pkg/events"
	"github.com/wailsapp/wails/v3/pkg/services/notifications"
)

//go:embed assets/*
var assets embed.FS

const trayDoubleClick = 400 * time.Millisecond

const (
	healthPollInterval        = 5 * time.Second
	healthFailureThreshold    = 3
	maxBackendRestartAttempts = 3
)

// App is the Wails service bound to the shell UI.
type App struct {
	app                *application.App
	window             *application.WebviewWindow
	settingsWindow     *application.WebviewWindow
	store              *settingsStore
	sleep              *sleepGuard
	cmd                *exec.Cmd
	monitorCancel      context.CancelFunc
	notifications      *notifications.NotificationService
	notificationMu     sync.Mutex
	notificationRoutes map[string]string
	mu                 sync.Mutex
	quitting           bool

	trayClickMu    sync.Mutex
	lastTrayClick  time.Time
	trayClickTimer *time.Timer
	drops          dropServer
	ownsListenPort bool
}

func (a *App) ServiceName() string { return "desktop" }

func (a *App) ServiceStartup(context.Context, application.ServiceOptions) error { return nil }

func (a *App) ServiceShutdown() error {
	a.sleep.stop()
	a.mu.Lock()
	if a.monitorCancel != nil {
		a.monitorCancel()
		a.monitorCancel = nil
	}
	cmd := a.cmd
	a.cmd = nil
	ownsPort := a.ownsListenPort
	a.ownsListenPort = false
	a.mu.Unlock()
	stopOctop(cmd)
	if ownsPort {
		clearDesktopPort()
	}
	return nil
}

func (a *App) GetSettings() Settings {
	return a.store.get()
}

func (a *App) GetSettingsStatus() (Settings, error) {
	s := a.store.get()
	if a.app == nil {
		return s, nil
	}
	status, err := a.app.Autostart.Status()
	if err != nil {
		return s, fmt.Errorf("read autostart status: %w", err)
	}
	if s.Autostart != status.Enabled {
		s.Autostart = status.Enabled
		if err := a.store.save(s); err != nil {
			return s, err
		}
	}
	return s, nil
}

func (a *App) SaveSettings(next Settings) (Settings, error) {
	cur, err := a.GetSettingsStatus()
	if err != nil {
		return cur, err
	}
	autostart, err := a.setAutostart(next.Autostart)
	if err != nil {
		return cur, err
	}
	if err := a.sleep.set(next.PreventSleep); err != nil {
		if _, rollbackErr := a.setAutostart(cur.Autostart); rollbackErr != nil {
			log.Printf("rollback autostart after sleep prevention failure: %v", rollbackErr)
		}
		return cur, err
	}
	next.Autostart = autostart
	if err := a.store.save(next); err != nil {
		return cur, err
	}
	saved := a.store.get()
	a.applyDashboardPrefs(saved)
	return saved, nil
}

func (a *App) ShowMain() {
	a.showWindow()
}

func (a *App) HideSettings() {
	if a.settingsWindow == nil {
		return
	}
	a.settingsWindow.Hide()
}

func (a *App) Quit() {
	a.requestQuit()
}

func (a *App) setAutostart(on bool) (bool, error) {
	if a.app == nil {
		return false, fmt.Errorf("autostart is unavailable before the application starts")
	}
	if on {
		if err := a.app.Autostart.Enable(); err != nil {
			return false, fmt.Errorf("enable autostart: %w", err)
		}
	} else if err := a.app.Autostart.Disable(); err != nil {
		return false, fmt.Errorf("disable autostart: %w", err)
	}
	status, err := a.app.Autostart.Status()
	if err != nil {
		return false, fmt.Errorf("read autostart status: %w", err)
	}
	if status.Enabled != on {
		return status.Enabled, fmt.Errorf("autostart state did not update")
	}
	return status.Enabled, nil
}

func (a *App) applyDashboardPrefs(s Settings) {
	if a.window == nil {
		return
	}
	js := fmt.Sprintf(
		`(function(){try{localStorage.setItem('octop:ui-locale',%s);}catch(e){}})();`,
		jsonString(string(s.Locale)),
	)
	a.window.ExecJS(js)
}

func jsonString(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

func (a *App) setStatus(msg string) {
	if a.app == nil {
		return
	}
	a.app.Event.Emit("desktop:status", msg)
}

func (a *App) boot() {
	locale := LocaleEN
	if a.store != nil {
		locale = a.store.get().Locale
	}
	if url := os.Getenv("OCTOP_DESKTOP_URL"); url != "" {
		a.setStatus(desktopText(locale, copyStatusConnecting))
		if err := waitHealth(locale, url, 60*time.Second); err != nil {
			a.setStatus(err.Error())
			return
		}
		a.showDashboard(url)
		return
	}
	a.setStatus(desktopText(locale, copyStatusCheckingRuntime))
	if err := ensurePortable(locale, a.setStatus); err != nil {
		a.setStatus(err.Error())
		return
	}
	root := portableDir()
	a.mu.Lock()
	stopOctop(a.cmd)
	a.cmd = nil
	a.mu.Unlock()
	host, preferred := resolveListen()
	if existing := findRunningDashboard(host, preferred); existing != "" {
		a.setStatus(desktopText(locale, copyStatusUsingExisting))
		if err := waitHealth(locale, existing, 30*time.Second); err != nil {
			a.setStatus(err.Error())
			return
		}
		a.showDashboard(existing)
		return
	}
	port, err := chooseFreePort(host, preferred)
	if err != nil {
		var busy noFreePortError
		var bind cannotBindError
		switch {
		case errors.As(err, &busy):
			a.setStatus(desktopText(locale, copyErrorNoFreePort, busy.From, busy.To))
		case errors.As(err, &bind):
			a.setStatus(desktopText(locale, copyErrorCannotBind, bind.Host, bind.Port, bind.Err.Error()))
		default:
			a.setStatus(err.Error())
		}
		return
	}
	a.mu.Lock()
	cmd, err := startOctop(root, port)
	a.cmd = cmd
	a.mu.Unlock()
	if err != nil {
		a.setStatus(err.Error())
		return
	}
	base := dashboardURL(host, port)
	a.setStatus(desktopText(locale, copyStatusStartingService))
	if err := waitHealth(locale, base, 2*time.Minute); err != nil {
		a.setStatus(err.Error())
		return
	}
	if err := writeDesktopPort(port); err != nil {
		log.Printf("desktop port: %v", err)
	} else {
		a.mu.Lock()
		a.ownsListenPort = true
		a.mu.Unlock()
	}
	a.showDashboard(base)
	a.startBackendMonitor(root, base, s.Port, locale)
}

func (a *App) startBackendMonitor(root, base string, port int, locale Locale) {
	a.mu.Lock()
	if a.monitorCancel != nil {
		a.monitorCancel()
	}
	ctx, cancel := context.WithCancel(context.Background())
	a.monitorCancel = cancel
	a.mu.Unlock()

	go a.monitorBackend(ctx, root, base, port, locale)
}

func (a *App) monitorBackend(ctx context.Context, root, base string, port int, locale Locale) {
	ticker := time.NewTicker(healthPollInterval)
	defer ticker.Stop()
	failures := 0
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if healthReady(base) {
				failures = 0
				continue
			}
			failures++
			if failures < healthFailureThreshold {
				continue
			}
			a.recoverBackend(ctx, root, base, port, locale)
			return
		}
	}
}

func (a *App) recoverBackend(ctx context.Context, root, base string, port int, locale Locale) {
	if a.window != nil {
		a.window.SetURL("/")
	}
	a.setStatus(desktopText(locale, copyStatusServiceLost))

	for attempt := 1; attempt <= maxBackendRestartAttempts; attempt++ {
		select {
		case <-ctx.Done():
			return
		default:
		}
		a.setStatus(desktopText(locale, copyStatusRestarting, attempt, maxBackendRestartAttempts))

		a.mu.Lock()
		if a.quitting {
			a.mu.Unlock()
			return
		}
		old := a.cmd
		a.cmd = nil
		a.mu.Unlock()
		stopOctop(old)

		cmd, err := startOctop(root, port)
		if err == nil {
			a.mu.Lock()
			stopping := a.quitting || ctx.Err() != nil
			if !stopping {
				a.cmd = cmd
			}
			a.mu.Unlock()
			if stopping {
				stopOctop(cmd)
				return
			}
			if waitHealth(locale, base, 30*time.Second) == nil {
				if ctx.Err() != nil {
					return
				}
				a.showDashboard(base)
				a.startBackendMonitor(root, base, port, locale)
				return
			}
		}
		if attempt < maxBackendRestartAttempts {
			timer := time.NewTimer(time.Duration(attempt) * 2 * time.Second)
			select {
			case <-ctx.Done():
				timer.Stop()
				return
			case <-timer.C:
			}
		}
	}
	a.mu.Lock()
	failed := a.cmd
	a.cmd = nil
	a.mu.Unlock()
	stopOctop(failed)
	a.setStatus(desktopText(locale, copyStatusRestartFailed, maxBackendRestartAttempts))
}

func (a *App) showDashboard(base string) {
	if a.window == nil {
		return
	}
	a.window.SetURL(base)
	a.scheduleDragOverlay()
	s := a.store.get()
	go func() {
		time.Sleep(800 * time.Millisecond)
		a.applyDashboardPrefs(s)
	}()
	a.setStatus(desktopText(s.Locale, copyStatusReady))
}

func (a *App) hideToTray() {
	if a.window == nil {
		return
	}
	a.window.Hide()
}

func (a *App) showWindow() {
	if a.window == nil {
		return
	}
	if a.window.IsMinimised() {
		a.window.UnMinimise()
	}
	a.window.Show()
	a.window.Focus()
}

func (a *App) toggleMainWindow() {
	if a.window == nil {
		return
	}
	if a.window.IsVisible() && !a.window.IsMinimised() {
		a.hideToTray()
		return
	}
	a.showWindow()
}

func (a *App) onTrayLeftClick() {
	a.trayClickMu.Lock()
	defer a.trayClickMu.Unlock()
	if a.trayClickTimer != nil {
		a.trayClickTimer.Stop()
		a.trayClickTimer = nil
	}
	now := time.Now()
	if !a.lastTrayClick.IsZero() && now.Sub(a.lastTrayClick) < trayDoubleClick {
		a.lastTrayClick = time.Time{}
		go a.toggleMainWindow()
		return
	}
	a.lastTrayClick = now
	a.trayClickTimer = time.AfterFunc(trayDoubleClick, func() {
		a.trayClickMu.Lock()
		a.trayClickTimer = nil
		a.trayClickMu.Unlock()
		a.showWindow()
	})
}

func (a *App) installDragOverlay() {
	if a.window == nil {
		return
	}
	a.window.ExecJS(dragOverlayJS())
	a.window.ExecJS(fileDropJS())
}

func (a *App) scheduleDragOverlay() {
	go func() {
		for range 40 {
			time.Sleep(250 * time.Millisecond)
			a.installDragOverlay()
			a.installExternalLinks()
		}
	}()
}

func (a *App) requestQuit() {
	a.mu.Lock()
	a.quitting = true
	a.mu.Unlock()
	if a.app != nil {
		a.app.Quit()
	}
}

func main() {
	notifier := notifications.New()
	store := &settingsStore{cur: loadSettings()}
	api := &App{
		store:              store,
		sleep:              &sleepGuard{},
		notifications:      notifier,
		notificationRoutes: make(map[string]string),
	}

	app := application.New(application.Options{
		Name:        "HomeMind",
		Description: "HomeMind desktop",
		Services: []application.Service{
			application.NewService(api),
			application.NewService(notifier),
		},
		Assets: application.AssetOptions{
			Handler: application.BundledAssetFileServer(assets),
		},
		Windows: application.WindowsOptions{
			DisableQuitOnLastWindowClosed: true,
		},
		Linux: application.LinuxOptions{
			DisableQuitOnLastWindowClosed: true,
		},
		Mac: application.MacOptions{
			ApplicationShouldTerminateAfterLastWindowClosed: false,
		},
	})
	api.app = app
	attachOpenURLEventListener(app, api.OpenExternal)
	attachDesktopNotificationListener(app, api.showDesktopNotification)
	notifier.OnNotificationResponse(func(result notifications.NotificationResult) {
		if result.Error == nil {
			api.activateNotification(result.Response.ID)
		}
	})
	app.Event.OnApplicationEvent(events.Common.ApplicationStarted, func(_ *application.ApplicationEvent) {
		applyAppIcon(app)
	})

	win := app.Window.NewWithOptions(application.WebviewWindowOptions{
		Title:                "HomeMind",
		Width:                1200,
		Height:               800,
		URL:                  "/",
		Frameless:            true,
		AllowSimpleEventEmit: true,
		EnableFileDrop:       true,
		BackgroundColour:     application.NewRGB(247, 248, 250),
	})
	api.window = win
	app.Event.On("desktop:toggle-maximise", func(_ *application.CustomEvent) {
		win.ToggleMaximise()
	})
	app.Event.On("desktop:minimise", func(_ *application.CustomEvent) {
		win.Minimise()
	})
	app.Event.On("desktop:close", func(_ *application.CustomEvent) {
		api.hideToTray()
	})
	var bootOnce sync.Once
	onMainWindowLoaded := func(_ *application.WindowEvent) {
		api.scheduleDragOverlay()
		bootOnce.Do(func() { go api.boot() })
	}
	win.OnWindowEvent(events.Mac.WebViewDidFinishNavigation, onMainWindowLoaded)
	win.OnWindowEvent(events.Windows.WebViewNavigationCompleted, onMainWindowLoaded)
	win.OnWindowEvent(events.Linux.WindowLoadFinished, onMainWindowLoaded)
	settingsWin := app.Window.NewWithOptions(application.WebviewWindowOptions{
		Title:            "HomeMind 设置",
		Width:            settingsWindowWidth,
		Height:           settingsWindowOuterHeight(),
		URL:              "/?settings=1",
		Hidden:           true,
		Frameless:        true,
		AlwaysOnTop:      true,
		DisableResize:    true,
		BackgroundColour: application.NewRGB(255, 255, 255),
		Windows: application.WindowsWindow{
			HiddenOnTaskbar: true,
		},
	})
	api.settingsWindow = settingsWin
	app.Event.RegisterApplicationEventHook(events.Mac.ApplicationShouldHandleReopen, func(event *application.ApplicationEvent) {
		event.Cancel()
		restoreMainAfterDockClick(api.window, api.settingsWindow)
	})

	win.RegisterHook(events.Common.WindowClosing, func(e *application.WindowEvent) {
		api.mu.Lock()
		quit := api.quitting
		api.mu.Unlock()
		if quit {
			return
		}
		e.Cancel()
		api.hideToTray()
	})
	settingsWin.RegisterHook(events.Common.WindowClosing, func(e *application.WindowEvent) {
		e.Cancel()
		settingsWin.Hide()
	})
	settingsWin.OnWindowEvent(events.Common.WindowLostFocus, func(_ *application.WindowEvent) {
		settingsWin.Hide()
	})

	tray := app.SystemTray.New()
	applyTrayIcon(tray)
	tray.SetTooltip("HomeMind")
	tray.AttachWindow(settingsWin).WindowOffset(6)
	showSettings := func() { tray.ShowWindow() }
	if trayLeftClickShowsSettings(runtime.GOOS) {
		tray.OnClick(showSettings)
	} else {
		tray.OnClick(func() { api.onTrayLeftClick() })
	}
	tray.OnRightClick(showSettings)

	if _, err := api.setAutostart(store.get().Autostart); err != nil {
		log.Printf("sync autostart: %v", err)
	}
	if err := api.sleep.set(store.get().PreventSleep); err != nil {
		log.Printf("enable sleep prevention: %v", err)
	}

	if err := app.Run(); err != nil {
		log.Fatal(err)
	}
}
