// Package config owns local contexts only; no server database or bucket access.
package config

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
)

type Context struct {
	Endpoint  string `json:"endpoint"`
	Token     string `json:"token"`
	Grant     string `json:"grant,omitempty"`
	Namespace string `json:"namespace_id,omitempty"`
}
type Config struct {
	Current  string             `json:"current"`
	Contexts map[string]Context `json:"contexts"`
}

func DefaultPath() (string, error) {
	root, e := os.UserConfigDir()
	if e != nil {
		return "", e
	}
	return filepath.Join(root, "wksctl", "contexts.json"), nil
}
func Load(path string) (Config, error) {
	value := Config{Contexts: map[string]Context{}}
	info, e := os.Lstat(path)
	if os.IsNotExist(e) {
		return value, nil
	}
	if e != nil {
		return value, e
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return value, errors.New("context file must be a regular private file")
	}
	if e := CheckPrivate(path); e != nil {
		return value, e
	}
	raw, e := os.ReadFile(path)
	if e != nil {
		return value, e
	}
	e = json.Unmarshal(raw, &value)
	if value.Contexts == nil {
		value.Contexts = map[string]Context{}
	}
	return value, e
}
func Save(path string, value Config) error {
	root := filepath.Dir(path)
	if e := os.MkdirAll(root, 0700); e != nil {
		return e
	}
	if info, e := os.Lstat(root); e != nil || info.Mode()&os.ModeSymlink != 0 {
		return errors.New("context directory must not be a symlink")
	}
	raw, e := json.MarshalIndent(value, "", "  ")
	if e != nil {
		return e
	}
	stream, e := os.CreateTemp(root, ".contexts-")
	if e != nil {
		return e
	}
	defer os.Remove(stream.Name())
	if e = SecurePrivate(stream.Name()); e != nil {
		stream.Close()
		return e
	}
	if _, e = stream.Write(raw); e != nil {
		stream.Close()
		return e
	}
	if e = stream.Sync(); e != nil {
		stream.Close()
		return e
	}
	if e = stream.Close(); e != nil {
		return e
	}
	return os.Rename(stream.Name(), path)
}
