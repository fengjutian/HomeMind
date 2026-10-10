package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDefaultSettingsMinimisesToTaskbar(t *testing.T) {
	if defaultSettings().MinimizeToTray {
		t.Fatal("minimize to tray should be disabled by default")
	}
}

func TestLoadSettingsMigratesLegacyPreventSleepMac(t *testing.T) {
	temp := t.TempDir()
	t.Setenv("OCTOP_HOME", temp)
	if err := os.WriteFile(
		filepath.Join(temp, "desktop-settings.json"),
		[]byte(`{"preventSleepMac":true}`),
		0o644,
	); err != nil {
		t.Fatal(err)
	}

	if !loadSettings().PreventSleep {
		t.Fatal("legacy preventSleepMac should migrate to preventSleep")
	}
}
