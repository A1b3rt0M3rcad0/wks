// Package wks implements the public WKS HTTP contract without server dependencies.
package wks

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type Client struct {
	Endpoint string       `json:"endpoint"`
	Token    string       `json:"-"`
	Grant    string       `json:"-"`
	HTTP     *http.Client `json:"-"`
}

func (c *Client) String() string   { return "WKS HTTP client (credentials redacted)" }
func (c *Client) GoString() string { return c.String() }

type Error struct {
	Code         string `json:"code"`
	Message      string `json:"message"`
	RequestID    string `json:"request_id,omitempty"`
	Retryable    bool   `json:"retryable"`
	WriteOutcome string `json:"write_outcome,omitempty"`
	Status       int    `json:"-"`
	OperationKey string `json:"operation_key,omitempty"`
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }

type Namespace struct {
	ID       string `json:"id"`
	Title    string `json:"title"`
	Revision int    `json:"revision,omitempty"`
}
type Version struct {
	ID               string `json:"id"`
	Revision         int    `json:"revision_number"`
	Committed        bool   `json:"committed"`
	Checksum         string `json:"checksum"`
	RepresentationID string `json:"representation_id"`
}
type Source struct {
	ID           string         `json:"id"`
	NamespaceID  string         `json:"namespace_id"`
	Title        string         `json:"title"`
	State        string         `json:"state"`
	Availability string         `json:"availability"`
	Versions     []Version      `json:"versions"`
	Revision     int            `json:"revision"`
	Metadata     map[string]any `json:"metadata"`
}
type Receipt struct {
	SourceID     string `json:"source_id"`
	VersionID    string `json:"source_version_id"`
	UploadID     string `json:"upload_id"`
	OperationID  string `json:"operation_id"`
	Status       string `json:"status"`
	WriteOutcome string `json:"write_outcome"`
}
type Operation struct {
	ID               string         `json:"operation_id"`
	State            string         `json:"processing_state"`
	RepresentationID string         `json:"published_representation_id"`
	ErrorCode        string         `json:"error_code"`
	Coverage         map[string]any `json:"coverage"`
}
type Page[T any] struct {
	Items      []T     `json:"items"`
	NextCursor *string `json:"next_cursor"`
	Complete   bool    `json:"complete"`
}
type Block struct {
	ID        string         `json:"id"`
	Type      string         `json:"type"`
	Text      string         `json:"text"`
	Locator   map[string]any `json:"locator"`
	AssetRefs []string       `json:"asset_refs"`
	Ordinal   int            `json:"ordinal"`
}
type Read struct {
	RepresentationID string  `json:"representation_id"`
	VersionID        string  `json:"source_version_id"`
	Blocks           []Block `json:"blocks"`
	Markdown         string  `json:"markdown"`
	NextCursor       *string `json:"next_cursor"`
	Complete         bool    `json:"complete"`
}
type Hit struct {
	SourceID         string  `json:"source_id"`
	VersionID        string  `json:"source_version_id"`
	RepresentationID string  `json:"representation_id"`
	SegmentRef       string  `json:"segment_ref"`
	Title            string  `json:"title"`
	Snippet          string  `json:"snippet"`
	Rank             float64 `json:"rank"`
}
type Search struct {
	Query      string  `json:"query"`
	Items      []Hit   `json:"items"`
	NextCursor *string `json:"next_cursor"`
	Complete   bool    `json:"complete"`
}
type Export struct {
	ID               string `json:"id"`
	RepresentationID string `json:"representation_id"`
	Format           string `json:"format"`
	Status           string `json:"status"`
	SHA256           string `json:"sha256"`
	ByteSize         int64  `json:"byte_size"`
	ContentPath      string `json:"content_path"`
}

func New(endpoint, token string) (*Client, error) {
	u, e := url.Parse(endpoint)
	if e != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return nil, errors.New("invalid API endpoint")
	}
	return &Client{Endpoint: strings.TrimRight(endpoint, "/"), Token: token, HTTP: &http.Client{Timeout: 30 * time.Second, CheckRedirect: func(req *http.Request, via []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}
func OperationKey() string {
	b := make([]byte, 16)
	if _, e := rand.Read(b); e != nil {
		panic(e)
	}
	return "wksctl-" + hex.EncodeToString(b)
}
func (c *Client) Request(ctx context.Context, method, path, key string, body io.Reader, contentType string) (*http.Response, error) {
	return c.requestHeaders(ctx, method, path, key, body, contentType, nil)
}
func (c *Client) requestHeaders(ctx context.Context, method, path, key string, body io.Reader, contentType string, headers map[string]string) (*http.Response, error) {
	if !strings.HasPrefix(path, "/") || strings.HasPrefix(path, "//") {
		return nil, errors.New("invalid API path")
	}
	req, e := http.NewRequestWithContext(ctx, method, c.Endpoint+path, body)
	if e != nil {
		return nil, e
	}
	req.Header.Set("Authorization", "Bearer "+c.Token)
	if c.Grant != "" {
		req.Header.Set("X-WKS-Grant", c.Grant)
	}
	if key != "" {
		req.Header.Set("Idempotency-Key", key)
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	for name, value := range headers {
		if name != "If-Match" && name != "If-None-Match" && name != "If-Range" && name != "Range" && name != "X-WKS-Confirmation" {
			return nil, errors.New("unsupported request header")
		}
		req.Header.Set(name, value)
	}
	if method != "GET" && method != "HEAD" {
		req.GetBody = nil
		if req.Body == nil || req.Body == http.NoBody {
			req.Body = io.NopCloser(bytes.NewReader(nil))
			req.ContentLength = -1
		}
	}
	response, e := c.HTTP.Do(req)
	if e != nil {
		outcome := "rejected"
		if method != "GET" && method != "HEAD" {
			outcome = "unknown"
		}
		return nil, &Error{Code: "transport.unavailable", Message: "Request outcome could not be confirmed", Retryable: true, WriteOutcome: outcome, OperationKey: key}
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		defer response.Body.Close()
		outcome := "rejected"
		if response.StatusCode >= 500 && method != "GET" && method != "HEAD" {
			outcome = "unknown"
		}
		failure := &Error{Status: response.StatusCode, Code: "http.error", Message: http.StatusText(response.StatusCode), WriteOutcome: outcome, OperationKey: key}
		_ = json.NewDecoder(io.LimitReader(response.Body, 65536)).Decode(failure)
		return nil, failure
	}
	return response, nil
}
func (c *Client) Do(ctx context.Context, method, path, key string, body, result any) error {
	var input io.Reader
	if body != nil {
		b, e := json.Marshal(body)
		if e != nil {
			return e
		}
		input = bytes.NewReader(b)
	}
	r, e := c.Request(ctx, method, path, key, input, "application/json")
	if e != nil {
		return e
	}
	defer r.Body.Close()
	if result == nil {
		_, e = io.Copy(io.Discard, r.Body)
		return e
	}
	e = json.NewDecoder(io.LimitReader(r.Body, 8<<20)).Decode(result)
	if e != nil {
		outcome := "rejected"
		if method != "GET" && method != "HEAD" {
			outcome = "unknown"
		}
		return &Error{Code: "transport.response_invalid", Message: "Response could not be confirmed", WriteOutcome: outcome, OperationKey: key, Retryable: true}
	}
	return nil
}
func (c *Client) CreateNamespace(ctx context.Context, title, key string) (Namespace, error) {
	var v Namespace
	e := c.Do(ctx, "POST", "/v1/namespaces", key, map[string]any{"title": title}, &v)
	return v, e
}
func (c *Client) CreateText(ctx context.Context, namespace, title, text, key string) (Receipt, error) {
	var v Receipt
	e := c.Do(ctx, "POST", "/v1/sources", key, map[string]any{"namespace_id": namespace, "kind": "text", "title": title, "text": text}, &v)
	return v, e
}
func (c *Client) GetSource(ctx context.Context, id string) (Source, error) {
	var v Source
	e := c.Do(ctx, "GET", "/v1/sources/"+url.PathEscape(id), "", nil, &v)
	return v, e
}
func (c *Client) GetOperation(ctx context.Context, id string) (Operation, error) {
	var v Operation
	e := c.Do(ctx, "GET", "/v1/operations/"+url.PathEscape(id), "", nil, &v)
	return v, e
}
func (c *Client) WaitOperation(ctx context.Context, id string) (Operation, error) {
	for {
		v, e := c.GetOperation(ctx, id)
		if e != nil {
			return v, e
		}
		switch v.State {
		case "succeeded", "partial":
			return v, nil
		case "failed", "cancelled":
			return v, &Error{Code: v.ErrorCode, Message: "Processing did not complete"}
		}
		select {
		case <-ctx.Done():
			return v, &Error{Code: "operation.timeout", Message: "Polling timed out", Retryable: true, WriteOutcome: "unknown"}
		case <-time.After(500 * time.Millisecond):
		}
	}
}
func (c *Client) Search(ctx context.Context, query, mode string, filters map[string]any) (Search, error) {
	if mode == "" {
		mode = "lexical"
	}
	var v Search
	e := c.Do(ctx, "POST", "/v1/search", "", map[string]any{"query": query, "mode": mode, "filters": filters}, &v)
	return v, e
}
func (c *Client) Read(ctx context.Context, id, cursor string) (Read, error) {
	var v Read
	path := "/v1/representations/" + url.PathEscape(id) + "/read"
	if cursor != "" {
		path += "?cursor=" + url.QueryEscape(cursor)
	}
	e := c.Do(ctx, "GET", path, "", nil, &v)
	return v, e
}
func (c *Client) CreateExport(ctx context.Context, id, format, key string) (Export, error) {
	var v Export
	e := c.Do(ctx, "POST", "/v1/representations/"+url.PathEscape(id)+"/exports", key, map[string]any{"format": format}, &v)
	return v, e
}
func (c *Client) Receipt(ctx context.Context, key string) (map[string]any, error) {
	v := map[string]any{}
	e := c.Do(ctx, "GET", "/v1/receipts/"+url.PathEscape(key), "", nil, &v)
	return v, e
}
