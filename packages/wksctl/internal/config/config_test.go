package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestPrivateAtomicContextsAndSymlinkRejection(t *testing.T) {
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
	link := filepath.Join(root, "symlink")
	if e = os.Symlink(path, link); e != nil {
		t.Fatal(e)
	}
	if _, e = Load(link); e == nil {
		t.Fatal("symlink credential accepted")
	}
	if e = makePublic(path); e != nil {
		t.Fatal(e)
	}
	if _, e = Load(path); e == nil {
		t.Fatal("public credentials accepted")
	}
}
