package main

import (
	"encoding/json"
	"log"
	"net/url"
	"reflect"
	"strings"
	"sync"
	"time"
	"unsafe"

	"github.com/wailsapp/wails/v3/pkg/application"
	"github.com/wailsapp/wails/v3/pkg/services/notifications"
)

const desktopNotificationEventPrefix = "desktop:notification:"

type desktopNotification struct {
	ID       string `json:"id"`
	Title    string `json:"title"`
	Body     string `json:"body"`
	AgentID  string `json:"agent_id"`
	ThreadID string `json:"thread_id"`
}

type desktopNotificationListener struct{ deliver func(desktopNotification) }

func (l *desktopNotificationListener) DispatchWailsEvent(event *application.CustomEvent) {
	if event == nil || !strings.HasPrefix(event.Name, desktopNotificationEventPrefix) {
		return
	}
	raw, err := url.QueryUnescape(strings.TrimPrefix(event.Name, desktopNotificationEventPrefix))
	if err != nil {
		return
	}
	var item desktopNotification
	if json.Unmarshal([]byte(raw), &item) != nil || item.ID == "" || item.Title == "" || item.ThreadID == "" {
		return
	}
	l.deliver(item)
}

func attachDesktopNotificationListener(app *application.App, deliver func(desktopNotification)) {
	root := reflect.ValueOf(app).Elem()
	lockField := root.FieldByName("wailsEventListenerLock")
	listField := root.FieldByName("wailsEventListeners")
	if !lockField.IsValid() || !listField.IsValid() {
		log.Printf("desktop notification: Wails event listener field missing")
		return
	}
	lock := reflect.NewAt(lockField.Type(), unsafe.Pointer(lockField.UnsafeAddr())).Interface().(*sync.Mutex)
	lock.Lock()
	defer lock.Unlock()
	list := reflect.NewAt(listField.Type(), unsafe.Pointer(listField.UnsafeAddr())).Elem()
	var listener application.WailsEventListener = &desktopNotificationListener{deliver: deliver}
	list.Set(reflect.Append(list, reflect.ValueOf(listener)))
}

func quietNow(settings Settings, now time.Time) bool {
	parse := func(raw string) (int, bool) {
		value, err := time.Parse("15:04", raw)
		return value.Hour()*60 + value.Minute(), err == nil
	}
	from, okFrom := parse(settings.NotificationQuietFrom)
	to, okTo := parse(settings.NotificationQuietTo)
	if !okFrom || !okTo || from == to {
		return false
	}
	minute := now.Hour()*60 + now.Minute()
	if from < to {
		return minute >= from && minute < to
	}
	return minute >= from || minute < to
}

func (a *App) showDesktopNotification(item desktopNotification) {
	settings := a.store.get()
	if !settings.DesktopNotifications || quietNow(settings, time.Now()) || a.window == nil {
		return
	}
	if a.window.IsVisible() && a.window.IsFocused() {
		return
	}
	a.notificationMu.Lock()
	if _, exists := a.notificationRoutes[item.ID]; exists {
		a.notificationMu.Unlock()
		return
	}
	if len(a.notificationRoutes) >= 256 {
		for id := range a.notificationRoutes {
			delete(a.notificationRoutes, id)
			break
		}
	}
	a.notificationRoutes[item.ID] = "/chat/" + url.PathEscape(item.AgentID) + "/" + url.PathEscape(item.ThreadID)
	a.notificationMu.Unlock()
	body := item.Body
	if !settings.NotificationShowBody {
		if settings.Locale == LocaleZH {
			body = "打开 HomeMind 查看消息。"
		} else {
			body = "Open HomeMind to view this message."
		}
	}
	if err := a.notifications.SendNotification(notifications.NotificationOptions{
		ID: item.ID, Title: item.Title, Body: body, ThreadID: item.ThreadID,
	}); err != nil {
		a.notificationMu.Lock()
		delete(a.notificationRoutes, item.ID)
		a.notificationMu.Unlock()
		log.Printf("desktop notification: %v", err)
	}
}

func (a *App) activateNotification(id string) {
	a.notificationMu.Lock()
	route := a.notificationRoutes[id]
	delete(a.notificationRoutes, id)
	a.notificationMu.Unlock()
	if route == "" || a.window == nil {
		return
	}
	host, _ := resolveListen()
	a.window.SetURL(dashboardURL(host, a.store.get().Port) + strings.TrimPrefix(route, "/"))
	a.showWindow()
}
