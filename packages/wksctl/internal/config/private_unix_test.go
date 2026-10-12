//go:build !windows

package config

import "os"

func makePublic(path string) error { return os.Chmod(path, 0644) }
