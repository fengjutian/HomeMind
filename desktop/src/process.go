package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
)

func mustEnv(cmd *exec.Cmd, extra map[string]string) {
	cmd.Env = os.Environ()
	for key, value := range extra {
		cmd.Env = append(cmd.Env, key+"="+value)
	}
}

func startOctop(root string, port int) (*exec.Cmd, error) {
	py := pythonExe(root)
	launch := filepath.Join(root, "launch.py")
	cmd := exec.Command(py, launch, "run", "--host", "127.0.0.1", "--port", strconv.Itoa(port))
	cmd.Dir = root
	mustEnv(cmd, map[string]string{
		"OCTOP_HOME":           octopHome(),
		"OCTOP_GREEN_PACKAGES": filepath.Join(root, "packages"),
		"PYTHONNOUSERSITE":     "1",
		"PYTHONPATH":           "",
	})
	configureProcGroup(cmd)
	logDir := filepath.Join(octopHome(), "logs")
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		return nil, fmt.Errorf("create desktop log directory: %w", err)
	}
	output, err := os.OpenFile(
		filepath.Join(logDir, "desktop-backend.log"),
		os.O_APPEND|os.O_CREATE|os.O_WRONLY,
		0o600,
	)
	if err != nil {
		return nil, fmt.Errorf("open desktop backend log: %w", err)
	}
	cmd.Stdout = output
	cmd.Stderr = output
	if err := cmd.Start(); err != nil {
		_ = output.Close()
		return nil, err
	}
	_ = output.Close()
	return cmd, nil
}

func stopOctop(cmd *exec.Cmd) {
	if cmd == nil || cmd.Process == nil {
		return
	}
	killProcessTree(cmd)
}

func dashboardURL(port int) string {
	return fmt.Sprintf("http://127.0.0.1:%d/", port)
}
