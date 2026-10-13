package cli

import (
	"bytes"
	"encoding/json"
	"github.com/A1b3rt0M3rcad0/wks/packages/wksctl/internal/config"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
)

func configured(t *testing.T, endpoint string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "contexts.json")
	if e := config.Save(path, config.Config{Current: "test", Contexts: map[string]config.Context{"test": {Endpoint: endpoint, Token: "private-credential", Namespace: "namespace"}}}); e != nil {
		t.Fatal(e)
	}
	return path
}
func TestOfflineInitPreservesAgentInstructions(t *testing.T) {
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "AGENTS.md"), []byte("existing instructions"), 0644)
	var out, stderr bytes.Buffer
	if code := Run([]string{"init", "--directory", root, "--agents"}, strings.NewReader(""), &out, &stderr, "test", "commit"); code != 0 {
		t.Fatal(stderr.String())
	}
	existing, _ := os.ReadFile(filepath.Join(root, "AGENTS.md"))
	if string(existing) != "existing instructions" {
		t.Fatal("agent instructions overwritten")
	}
	skill, _ := os.ReadFile(filepath.Join(root, ".agents", "skills", "wks", "SKILL.md"))
	if !strings.Contains(string(skill), "public HTTP gateway") {
		t.Fatal("skill absent")
	}
}
func TestDestructiveGuardAndDryRunNeverWrite(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1); w.Write([]byte(`{}`)) }))
	defer server.Close()
	path := configured(t, server.URL)
	for _, args := range [][]string{{"source", "delete", "--id", "source"}, {"source", "delete", "--id", "source", "--dry-run", "--yes"}} {
		var out, stderr bytes.Buffer
		code := Run(append(args, "--config", path), strings.NewReader(""), &out, &stderr, "test", "commit")
		if strings.Contains(out.String()+stderr.String(), "private-credential") {
			t.Fatal("credential exposed")
		}
		if code == 0 && !strings.Contains(out.String(), `"mutated": false`) {
			t.Fatal("destructive action silently accepted")
		}
	}
	if calls.Load() != 0 {
		t.Fatal("guard sent a request")
	}
}
func TestUnknownWriteExitCodeAndRecoveryKey(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		connection, _, _ := w.(http.Hijacker).Hijack()
		connection.Close()
	}))
	defer server.Close()
	path := configured(t, server.URL)
	var out, stderr bytes.Buffer
	code := Run([]string{"source", "create", "--text", "input", "--key", "stable-write", "--config", path}, strings.NewReader(""), &out, &stderr, "test", "commit")
	if code != 3 {
		t.Fatalf("unknown outcome exit=%d", code)
	}
	var failure map[string]any
	if e := json.Unmarshal(stderr.Bytes(), &failure); e != nil {
		t.Fatal(e)
	}
	if failure["operation_key"] != "stable-write" || failure["write_outcome"] != "unknown" {
		t.Fatal("unknown outcome lost")
	}
	if strings.Contains(stderr.String(), "private-credential") {
		t.Fatal("credential exposed")
	}
}
func TestManifestRejectsWorkspaceEscapesAndSecretsFields(t *testing.T) {
	root := t.TempDir()
	outside := filepath.Join(t.TempDir(), "private")
	os.WriteFile(outside, []byte("private"), 0600)
	if err := os.Symlink(outside, filepath.Join(root, "escape")); err != nil {
		t.Fatalf("symlink creation prerequisite unavailable; confinement test is required: %v", err)
	}
	if _, e := confinedFile(root, "escape"); e == nil {
		t.Fatal("symlink escaped workspace")
	}
	manifest := filepath.Join(root, "manifest.yaml")
	os.WriteFile(manifest, []byte("schema_version: 1\ntoken: private\n"), 0644)
	if _, _, e := loadManifest(manifest); e == nil {
		t.Fatal("credential field accepted")
	}
}
func TestCredentialFileRequiredBeforeIssuance(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1) }))
	defer server.Close()
	path := configured(t, server.URL)
	var out, stderr bytes.Buffer
	code := Run([]string{"credential", "create", "--id", "client", "--config", path}, strings.NewReader(""), &out, &stderr, "test", "commit")
	if code == 0 || calls.Load() != 0 {
		t.Fatal("credential issued without a private destination")
	}
}

func TestOfflineSkillInstallPreservesCustomizations(t *testing.T) {
	root := t.TempDir()
	var out, stderr bytes.Buffer
	run := func() int {
		out.Reset()
		stderr.Reset()
		return Run([]string{"skill", "install", "--directory", root}, strings.NewReader(""), &out, &stderr, "test", "commit")
	}
	if run() != 0 {
		t.Fatal(stderr.String())
	}
	if _, err := os.Stat(filepath.Join(root, ".wks")); !os.IsNotExist(err) {
		t.Fatal("skill installation initialized workspace")
	}
	if run() != 0 || !strings.Contains(out.String(), `"changed": false`) {
		t.Fatal("installation is not idempotent", stderr.String())
	}
	path := filepath.Join(root, ".agents", "skills", "wks", "SKILL.md")
	if err := os.WriteFile(path, []byte("custom skill"), 0600); err != nil {
		t.Fatal(err)
	}
	if run() == 0 {
		t.Fatal("custom skill overwritten")
	}
	raw, _ := os.ReadFile(path)
	if string(raw) != "custom skill" {
		t.Fatal("custom content lost")
	}
}
