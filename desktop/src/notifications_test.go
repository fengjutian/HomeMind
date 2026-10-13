package main

import (
	"testing"
	"time"
)

func TestQuietNowAcrossMidnight(t *testing.T) {
	settings := defaultSettings()
	for _, tc := range []struct {
		hour, minute int
		quiet        bool
	}{{23, 0, true}, {6, 59, true}, {7, 0, false}, {12, 0, false}} {
		now := time.Date(2026, 10, 13, tc.hour, tc.minute, 0, 0, time.Local)
		if got := quietNow(settings, now); got != tc.quiet {
			t.Fatalf("quietNow(%02d:%02d) = %v, want %v", tc.hour, tc.minute, got, tc.quiet)
		}
	}
}

func TestQuietNowSameTimesDisablesQuietHours(t *testing.T) {
	settings := defaultSettings()
	settings.NotificationQuietTo = settings.NotificationQuietFrom
	if quietNow(settings, time.Now()) {
		t.Fatal("matching quiet-hour boundaries should disable quiet hours")
	}
}
