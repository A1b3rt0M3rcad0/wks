package wks

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestUnknownWriteNeverAutomaticallyRetries(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		connection, _, _ := w.(http.Hijacker).Hijack()
		connection.Close()
	}))
	defer server.Close()
	client, _ := New(server.URL, "private-secret")
	_, err := client.CreateText(context.Background(), "scope", "title", "text", "stable-key")
	var failure *Error
	if !errors.As(err, &failure) || failure.WriteOutcome != "unknown" || failure.OperationKey != "stable-key" || calls.Load() != 1 {
		t.Fatalf("unexpected outcome: %+v calls=%d", failure, calls.Load())
	}
	if strings.Contains(err.Error(), "private-secret") {
		t.Fatal("secret exposed")
	}
}
func TestPrivateHeadersAndReadPagination(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer private" || r.Header.Get("X-WKS-Grant") != "scope-grant" {
			t.Error("private headers missing")
		}
		calls.Add(1)
		next := (*string)(nil)
		if r.URL.Query().Get("cursor") == "" {
			value := "cursor+/="
			next = &value
		}
		json.NewEncoder(w).Encode(Read{NextCursor: next})
	}))
	defer server.Close()
	client, _ := New(server.URL, "private")
	client.Grant = "scope-grant"
	pages := 0
	if err := client.EachRead(context.Background(), "rep", func(Read) error { pages++; return nil }); err != nil || pages != 2 || calls.Load() != 2 {
		t.Fatalf("pagination failed: %v %d %d", err, pages, calls.Load())
	}
}
func TestDownloadVerifiesIntegrityAndRejectsRedirect(t *testing.T) {
	content := []byte("original bytes")
	digest := sha256.Sum256(content)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/redirect" {
			http.Redirect(w, r, "https://example.invalid", http.StatusFound)
			return
		}
		w.Write(content)
	}))
	defer server.Close()
	client, _ := New(server.URL, "private")
	var destination bytes.Buffer
	n, err := client.Download(context.Background(), "/content", &destination, hex.EncodeToString(digest[:]))
	if err != nil || n != int64(len(content)) || !bytes.Equal(destination.Bytes(), content) {
		t.Fatal("stream failed")
	}
	if _, err = client.Download(context.Background(), "/content", io.Discard, strings.Repeat("0", 64)); err == nil {
		t.Fatal("checksum mismatch accepted")
	}
	if _, err = client.Download(context.Background(), "/redirect", io.Discard, ""); err == nil {
		t.Fatal("redirect accepted")
	}
}
func TestResumeStartsAtConfirmedOffset(t *testing.T) {
	content := []byte("already sent;rest")
	offset := int64(13)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "GET" {
			json.NewEncoder(w).Encode(Upload{ID: "upload", Offset: offset, ExpectedSize: int64(len(content))})
			return
		}
		body, _ := io.ReadAll(r.Body)
		if !bytes.Equal(body, content[offset:]) || r.URL.Query().Get("offset") != "13" {
			t.Error("resume retransmitted prior bytes")
		}
		digest := sha256.Sum256(body)
		if r.URL.Query().Get("checksum_sha256") != hex.EncodeToString(digest[:]) {
			t.Error("part checksum missing")
		}
		json.NewEncoder(w).Encode(Upload{ID: "upload", Offset: int64(len(content)), ExpectedSize: int64(len(content))})
	}))
	defer server.Close()
	client, _ := New(server.URL, "private")
	result, err := client.ResumeUpload(context.Background(), "upload", bytes.NewReader(content), nil)
	if err != nil || result.Offset != int64(len(content)) {
		t.Fatalf("resume failed: %v", err)
	}
}

// TestLivePublicGateway is activated by the HTTP-only consumer harness.
func TestLivePublicGateway(t *testing.T) {
	endpoint := os.Getenv("WKS_SDK_TEST_ENDPOINT")
	if endpoint == "" {
		t.Skip("live gateway fixture not configured")
	}
	client, e := New(endpoint, os.Getenv("WKS_SDK_TEST_TOKEN"))
	if e != nil {
		t.Fatal(e)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	created, e := client.CreateText(ctx, os.Getenv("WKS_SDK_TEST_NAMESPACE"), "Go consumer", "Independent Go public client resistance.", "go-live-write")
	if e != nil {
		t.Fatal(e)
	}
	receipt, e := client.Receipt(ctx, "go-live-write")
	if e != nil || receipt["source_id"] != created.SourceID {
		t.Fatal("receipt mismatch")
	}
	operation, e := client.WaitOperation(ctx, created.OperationID)
	if e != nil {
		t.Fatal(e)
	}
	page, e := client.Read(ctx, operation.RepresentationID, "")
	if e != nil || len(page.Blocks) == 0 {
		t.Fatal("read unavailable")
	}
	result, e := client.Search(ctx, "resistance", "lexical", map[string]any{"namespace_id": os.Getenv("WKS_SDK_TEST_NAMESPACE")})
	if e != nil || len(result.Items) == 0 {
		t.Fatal("scoped search unavailable")
	}
}
