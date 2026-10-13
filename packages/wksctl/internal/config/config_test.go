package config

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"syscall"
	"testing"
)

func TestPrivateAtomicContextsAndPermissions(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "private", "contexts.json")
	value := Config{Current: "test", Contexts: map[string]Context{"test": {Endpoint: "https://wks.example", Token: "secret"}}}
	if e := Save(path, value); e != nil {
		t.Fatal(e)
	}
	loaded, e := Load(path)
	if e != nil || loaded.Contexts["test"].Token != "secret" {
		t.Fatal("context not persisted")
	}
	if e = CheckPrivate(path); e != nil {
		t.Fatal("credential file is not private")
	}
	if e = makePublic(path); e != nil {
		t.Fatal(e)
	}
	if _, e = Load(path); e == nil {
		t.Fatal("public credentials accepted")
	}
}

func requireSymlink(t *testing.T, old, link string) {
	t.Helper()
	if err := os.Symlink(old, link); err != nil {
		if runtime.GOOS == "windows" && (errors.Is(err, syscall.Errno(1314)) || errors.Is(err, syscall.Errno(5))) {
			t.Fatalf("Windows symlink prerequisite missing: enable Developer Mode or run with SeCreateSymbolicLinkPrivilege; confinement tests are not skipped: %v", err)
		}
		t.Fatal(err)
	}
}

func TestSymlinkPrerequisiteFileAndDirectory(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "file")
	if err := os.WriteFile(path, []byte("probe"), 0600); err != nil {
		t.Fatal(err)
	}
	requireSymlink(t, path, filepath.Join(root, "file-link"))
	requireSymlink(t, root, filepath.Join(root, "directory-link"))
}

func TestContextRejectsSymlinkFileAndDirectory(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "private", "contexts.json")
	value := Config{Current: "test", Contexts: map[string]Context{"test": {Endpoint: "https://wks.example", Token: "secret"}}}
	if err := Save(path, value); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "file-link")
	requireSymlink(t, path, link)
	if _, err := Load(link); err == nil {
		t.Fatal("symlink credential file accepted")
	}
	directoryLink := filepath.Join(root, "directory-link")
	requireSymlink(t, filepath.Dir(path), directoryLink)
	alias := filepath.Join(directoryLink, "contexts.json")
	if _, err := Load(alias); err == nil {
		t.Fatal("symlink context directory accepted")
	}
	if err := Save(alias, value); err == nil {
		t.Fatal("symlink context directory written")
	}
	if _, err := Load(path); err != nil {
		t.Fatal("original private context damaged", err)
	}
}
