//go:build !windows

package config

import (
	"errors"
	"os"
)

func SecurePrivate(path string) error { return os.Chmod(path, 0600) }
func CheckPrivate(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 {
		return errors.New("credential file must be private (0600)")
	}
	return nil
}
