package cli

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	wks "github.com/A1b3rt0M3rcad0/wks/packages/wks-sdk-go"
	"github.com/A1b3rt0M3rcad0/wks/packages/wksctl/internal/config"
	"gopkg.in/yaml.v3"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type NamespaceManifest struct {
	Title       string `json:"title" yaml:"title"`
	ExternalRef string `json:"external_ref" yaml:"external_ref"`
}
type SourceManifest struct {
	ExternalRef string         `json:"external_ref" yaml:"external_ref"`
	Title       string         `json:"title" yaml:"title"`
	Kind        string         `json:"kind" yaml:"kind"`
	File        string         `json:"file,omitempty" yaml:"file,omitempty"`
	Text        string         `json:"text,omitempty" yaml:"text,omitempty"`
	URI         string         `json:"external_uri,omitempty" yaml:"external_uri,omitempty"`
	MIME        string         `json:"media_type,omitempty" yaml:"media_type,omitempty"`
	Description string         `json:"description,omitempty" yaml:"description,omitempty"`
	Tags        []string       `json:"tags,omitempty" yaml:"tags,omitempty"`
	Metadata    map[string]any `json:"metadata,omitempty" yaml:"metadata,omitempty"`
}
type CollectionManifest struct {
	ExternalRef string   `json:"external_ref" yaml:"external_ref"`
	Title       string   `json:"title" yaml:"title"`
	Sources     []string `json:"sources" yaml:"sources"`
}
type Manifest struct {
	SchemaVersion int                  `json:"schema_version" yaml:"schema_version"`
	Namespace     NamespaceManifest    `json:"namespace" yaml:"namespace"`
	Sources       []SourceManifest     `json:"sources" yaml:"sources"`
	Collections   []CollectionManifest `json:"collections" yaml:"collections"`
}
type Checkpoint struct {
	SchemaVersion int               `json:"schema_version"`
	NamespaceID   string            `json:"namespace_id"`
	Sources       map[string]string `json:"sources"`
	Collections   map[string]string `json:"collections"`
	Completed     map[string]bool   `json:"completed"`
}
type SourcePlan struct {
	Source      SourceManifest `json:"source"`
	SHA256      string         `json:"sha256"`
	Action      string         `json:"action"`
	SourceID    string         `json:"source_id,omitempty"`
	Path        string         `json:"-"`
	BaseVersion string         `json:"-"`
}

func loadManifest(path string) (Manifest, string, error) {
	var value Manifest
	absolute, e := filepath.Abs(path)
	if e != nil {
		return value, "", e
	}
	stream, e := os.Open(absolute)
	if e != nil {
		return value, "", errors.New("manifest cannot be read")
	}
	defer stream.Close()
	raw, e := io.ReadAll(io.LimitReader(stream, 2<<20+1))
	if e != nil || len(raw) > 2<<20 {
		return value, "", errors.New("manifest exceeds limit")
	}
	decoder := yaml.NewDecoder(bytes.NewReader(raw))
	decoder.KnownFields(true)
	if e = decoder.Decode(&value); e != nil {
		return value, "", errors.New("invalid manifest schema")
	}
	if value.SchemaVersion != 1 {
		return value, "", errors.New("supported manifest schema_version is 1")
	}
	if len(value.Sources) > 10000 || len(value.Collections) > 1000 {
		return value, "", errors.New("manifest item budget exceeded")
	}
	root := filepath.Dir(absolute)
	if filepath.Base(root) == ".wks" {
		root = filepath.Dir(root)
	}
	root, e = filepath.EvalSymlinks(root)
	if e != nil {
		return value, "", e
	}
	seen := map[string]bool{}
	for i := range value.Sources {
		source := &value.Sources[i]
		if source.ExternalRef == "" || len(source.ExternalRef) > 250 || seen[source.ExternalRef] {
			return value, "", errors.New("every source requires a unique external_ref")
		}
		seen[source.ExternalRef] = true
		if source.Kind == "" {
			if source.File != "" {
				source.Kind = "upload"
			} else {
				source.Kind = "text"
			}
		}
		if source.Kind != "text" && source.Kind != "upload" && source.Kind != "bookmark" {
			return value, "", errors.New("unsupported manifest source kind")
		}
		if source.Kind == "upload" && source.File == "" || source.Kind == "bookmark" && source.URI == "" {
			return value, "", errors.New("source content is required")
		}
		if source.Title == "" {
			source.Title = source.ExternalRef
		}
		if source.Tags == nil {
			source.Tags = []string{}
		}
		if source.Metadata == nil {
			source.Metadata = map[string]any{}
		}
	}
	collections := map[string]bool{}
	for _, collection := range value.Collections {
		if collection.ExternalRef == "" || collections[collection.ExternalRef] {
			return value, "", errors.New("collections require unique external_ref")
		}
		collections[collection.ExternalRef] = true
		for _, ref := range collection.Sources {
			if !seen[ref] {
				return value, "", errors.New("collection links an undeclared source")
			}
		}
	}
	return value, root, nil
}
func confinedFile(root, relative string) (string, error) {
	if filepath.IsAbs(relative) {
		return "", errors.New("manifest files must be relative to the workspace")
	}
	candidate, e := filepath.EvalSymlinks(filepath.Join(root, relative))
	if e != nil {
		return "", errors.New("manifest file cannot be resolved")
	}
	relation, e := filepath.Rel(root, candidate)
	if e != nil || relation == ".." || strings.HasPrefix(relation, ".."+string(filepath.Separator)) {
		return "", errors.New("manifest file escapes the workspace")
	}
	info, e := os.Stat(candidate)
	if e != nil || !info.Mode().IsRegular() {
		return "", errors.New("manifest input must be a regular file")
	}
	return candidate, nil
}
func sourceDigest(root string, source SourceManifest) (string, string, error) {
	if source.File == "" {
		sum := sha256.Sum256([]byte(source.Text + source.URI))
		return hex.EncodeToString(sum[:]), "", nil
	}
	path, e := confinedFile(root, source.File)
	if e != nil {
		return "", "", e
	}
	stream, e := os.Open(path)
	if e != nil {
		return "", "", e
	}
	defer stream.Close()
	hash := sha256.New()
	if _, e = io.Copy(hash, stream); e != nil {
		return "", "", e
	}
	return hex.EncodeToString(hash.Sum(nil)), path, nil
}
func checkpointLoad(path string) (Checkpoint, error) {
	value := Checkpoint{SchemaVersion: 1, Sources: map[string]string{}, Collections: map[string]string{}, Completed: map[string]bool{}}
	raw, e := os.ReadFile(path)
	if os.IsNotExist(e) {
		return value, nil
	}
	if e != nil {
		return value, e
	}
	if e = json.Unmarshal(raw, &value); e != nil {
		return value, errors.New("invalid checkpoint; existing file preserved")
	}
	if value.SchemaVersion != 1 || value.Sources == nil || value.Collections == nil || value.Completed == nil {
		return value, errors.New("invalid checkpoint schema")
	}
	return value, nil
}
func checkpointSave(path string, value Checkpoint) error {
	raw, e := json.MarshalIndent(value, "", "  ")
	if e != nil {
		return e
	}
	root := filepath.Dir(path)
	if e = os.MkdirAll(root, 0700); e != nil {
		return e
	}
	stream, e := os.CreateTemp(root, ".checkpoint-")
	if e != nil {
		return e
	}
	defer os.Remove(stream.Name())
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
func stableKey(kind, ref, digest string) string {
	sum := sha256.Sum256([]byte(kind + "\x00" + ref + "\x00" + digest))
	return "wks-sync-" + hex.EncodeToString(sum[:])
}
func listScoped(ctx context.Context, client *wks.Client, namespace string) (map[string]map[string]any, error) {
	values := map[string]map[string]any{}
	path := "/v1/sources?namespace_id=" + url.QueryEscape(namespace) + "&page_size=100"
	seen := map[string]bool{}
	for {
		var page struct {
			Items  []map[string]any `json:"items"`
			Cursor string           `json:"next_cursor"`
		}
		if e := client.Do(ctx, "GET", path, "", nil, &page); e != nil {
			return nil, e
		}
		for _, source := range page.Items {
			if ref, ok := source["external_ref"].(string); ok {
				values[ref] = source
			}
		}
		if page.Cursor == "" {
			return values, nil
		}
		if seen[page.Cursor] {
			return nil, errors.New("server repeated a cursor")
		}
		seen[page.Cursor] = true
		path = "/v1/sources?namespace_id=" + url.QueryEscape(namespace) + "&page_size=100&cursor=" + url.QueryEscape(page.Cursor)
	}
}
func currentChecksum(source map[string]any) string {
	current, _ := source["current_version_id"].(string)
	versions, _ := source["versions"].([]any)
	for _, item := range versions {
		version, ok := item.(map[string]any)
		if ok && version["id"] == current {
			value, _ := version["checksum"].(string)
			return value
		}
	}
	return ""
}
func manifestRun(ctx context.Context, client *wks.Client, option options, current config.Context, output, stderr io.Writer, name string) int {
	path := option.values["manifest"]
	if path == "" {
		path = ".wks/manifest.json"
	}
	manifest, root, e := loadManifest(path)
	if e != nil {
		return failure(stderr, e)
	}
	checkpointPath := filepath.Join(root, ".wks", "checkpoints.json")
	checkpoint, e := checkpointLoad(checkpointPath)
	if e != nil {
		return failure(stderr, e)
	}
	namespace := current.Namespace
	if namespace == "" {
		namespace = checkpoint.NamespaceID
	}
	if namespace != "" && checkpoint.NamespaceID != "" && namespace != checkpoint.NamespaceID {
		return failure(stderr, errors.New("checkpoint belongs to another namespace; use a separate workspace"))
	}
	existing := map[string]map[string]any{}
	if namespace != "" {
		existing, e = listScoped(ctx, client, namespace)
		if e != nil {
			return failure(stderr, e)
		}
	}
	plans := []SourcePlan{}
	for _, source := range manifest.Sources {
		checksum, file, e := sourceDigest(root, source)
		if e != nil {
			return failure(stderr, e)
		}
		action := "create"
		identity := ""
		baseVersion := ""
		if old, found := existing[source.ExternalRef]; found {
			identity, _ = old["id"].(string)
			baseVersion, _ = old["current_version_id"].(string)
			action = "new_version"
			if source.Kind == "bookmark" || currentChecksum(old) == checksum {
				action = "metadata_or_unchanged"
			}
		}
		plans = append(plans, SourcePlan{Source: source, SHA256: checksum, Action: action, SourceID: identity, Path: file, BaseVersion: baseVersion})
	}
	if name == "plan" || option.flags["dry-run"] {
		summary := []map[string]any{}
		for _, plan := range plans {
			summary = append(summary, map[string]any{"external_ref": plan.Source.ExternalRef, "action": plan.Action, "sha256": plan.SHA256, "source_id": plan.SourceID})
		}
		emit(output, map[string]any{"schema_version": 1, "namespace_id": namespace, "sources": summary, "collections": len(manifest.Collections), "prune": false, "mutated": false})
		return 0
	}
	if !option.flags["yes"] {
		return failure(stderr, errors.New("inspect plan first, then use --yes; pruning is never implicit"))
	}
	if namespace == "" {
		if manifest.Namespace.ExternalRef == "" {
			return failure(stderr, errors.New("new namespace requires an external_ref"))
		}
		title := manifest.Namespace.Title
		if title == "" {
			title = "Workspace"
		}
		var value wks.Namespace
		e = client.Do(ctx, "POST", "/v1/namespaces", stableKey("namespace", manifest.Namespace.ExternalRef, title), map[string]any{"title": title, "external_ref": manifest.Namespace.ExternalRef}, &value)
		if e != nil {
			return failure(stderr, e)
		}
		namespace = value.ID
		checkpoint.NamespaceID = namespace
		if e = checkpointSave(checkpointPath, checkpoint); e != nil {
			return failure(stderr, e)
		}
	}
	checkpoint.NamespaceID = namespace
	for _, plan := range plans {
		source := plan.Source
		identity := plan.SourceID
		key := stableKey("source", namespace+":"+source.ExternalRef, plan.SHA256)
		if plan.Action == "new_version" && plan.BaseVersion != "" {
			key = stableKey("source-version", namespace+":"+source.ExternalRef+":"+plan.BaseVersion, plan.SHA256)
		}
		if plan.Action != "metadata_or_unchanged" {
			body := map[string]any{"namespace_id": namespace, "external_ref": source.ExternalRef, "title": source.Title, "description": source.Description, "tags": source.Tags, "metadata": source.Metadata, "kind": source.Kind}
			if source.Kind == "bookmark" {
				body["external_uri"] = source.URI
			} else if source.Kind == "text" {
				value := source.Text
				if plan.Path != "" {
					raw, e := os.ReadFile(plan.Path)
					if e != nil || len(raw) > 2<<20 {
						return failure(stderr, errors.New("inline text exceeds limit; use upload kind"))
					}
					value = string(raw)
				}
				body["text"] = value
			} else {
				info, e := os.Stat(plan.Path)
				if e != nil {
					return failure(stderr, e)
				}
				mime := source.MIME
				if mime == "" {
					mime = "application/octet-stream"
				}
				body["upload"] = map[string]any{"filename": filepath.Base(plan.Path), "declared_media_type": mime, "byte_size": info.Size(), "checksum_sha256": plan.SHA256}
			}
			endpoint := "/v1/sources"
			if identity != "" {
				endpoint += "/" + url.PathEscape(identity) + "/versions"
				body = map[string]any{"text": body["text"], "upload": body["upload"]}
				for name, value := range body {
					if value == nil {
						delete(body, name)
					}
				}
			}
			var receipt wks.Receipt
			e = client.Do(ctx, "GET", "/v1/receipts/"+url.PathEscape(key), "", nil, &receipt)
			if e != nil {
				var remote *wks.Error
				if !errors.As(e, &remote) || remote.Status != 404 {
					return failure(stderr, e)
				}
				if e = client.Do(ctx, "POST", endpoint, key, body, &receipt); e != nil {
					return failure(stderr, e)
				}
			}
			identity = receipt.SourceID
			checkpoint.Sources[source.ExternalRef] = identity
			if e = checkpointSave(checkpointPath, checkpoint); e != nil {
				return failure(stderr, e)
			}
			if receipt.UploadID != "" {
				stream, e := os.Open(plan.Path)
				if e != nil {
					return failure(stderr, e)
				}
				_, e = client.ResumeUpload(ctx, receipt.UploadID, stream, nil)
				stream.Close()
				if e != nil {
					return failure(stderr, e)
				}
				if e = client.Do(ctx, "POST", "/v1/uploads/"+url.PathEscape(receipt.UploadID)+"/commit", key+"-commit", nil, &receipt); e != nil {
					return failure(stderr, e)
				}
			}
		}
		currentSource := map[string]any{}
		if e = client.Do(ctx, "GET", "/v1/sources/"+url.PathEscape(identity), "", nil, &currentSource); e != nil {
			return failure(stderr, e)
		}
		desired := map[string]any{"title": source.Title, "description": source.Description, "tags": source.Tags, "metadata": source.Metadata}
		changed := false
		for field, value := range desired {
			left, _ := json.Marshal(value)
			right, _ := json.Marshal(currentSource[field])
			if !bytes.Equal(left, right) {
				changed = true
			}
		}
		if changed {
			raw, _ := json.Marshal(desired)
			metadataKey := stableKey("metadata", identity, string(raw))
			revision := strconvNumber(currentSource["revision"])
			var result map[string]any
			if e = client.DoHeaders(ctx, "PATCH", "/v1/sources/"+url.PathEscape(identity), metadataKey, desired, map[string]string{"If-Match": revision}, &result); e != nil {
				return failure(stderr, e)
			}
		}
		checkpoint.Sources[source.ExternalRef] = identity
		checkpoint.Completed[key] = true
		if e = checkpointSave(checkpointPath, checkpoint); e != nil {
			return failure(stderr, e)
		}
	}
	for _, collection := range manifest.Collections {
		identity := checkpoint.Collections[collection.ExternalRef]
		if identity == "" {
			var value map[string]any
			if e = client.Do(ctx, "POST", "/v1/collections", stableKey("collection", namespace+":"+collection.ExternalRef, collection.Title), map[string]any{"namespace_id": namespace, "title": collection.Title}, &value); e != nil {
				return failure(stderr, e)
			}
			identity, _ = value["id"].(string)
			checkpoint.Collections[collection.ExternalRef] = identity
			if e = checkpointSave(checkpointPath, checkpoint); e != nil {
				return failure(stderr, e)
			}
		}
		var currentCollection map[string]any
		if e = client.Do(ctx, "GET", "/v1/collections/"+url.PathEscape(identity), "", nil, &currentCollection); e != nil {
			return failure(stderr, e)
		}
		if currentCollection["title"] != collection.Title {
			var patched map[string]any
			if e = client.DoHeaders(ctx, "PATCH", "/v1/collections/"+url.PathEscape(identity), stableKey("collection-metadata", identity, collection.Title), map[string]any{"title": collection.Title}, map[string]string{"If-Match": strconvNumber(currentCollection["revision"])}, &patched); e != nil {
				return failure(stderr, e)
			}
		}
		for _, ref := range collection.Sources {
			var result map[string]any
			sourceID := checkpoint.Sources[ref]
			if e = client.Do(ctx, "PUT", "/v1/collections/"+url.PathEscape(identity)+"/sources/"+url.PathEscape(sourceID), stableKey("link", identity, sourceID), nil, &result); e != nil {
				return failure(stderr, e)
			}
		}
	}
	refs := make([]string, 0, len(checkpoint.Sources))
	for ref := range checkpoint.Sources {
		refs = append(refs, ref)
	}
	sort.Strings(refs)
	emit(output, map[string]any{"namespace_id": namespace, "sources_applied": len(plans), "external_refs": refs, "checkpoint": checkpointPath, "prune": false, "complete": true})
	return 0
}
func strconvNumber(value any) string { raw, _ := json.Marshal(value); return string(raw) }
