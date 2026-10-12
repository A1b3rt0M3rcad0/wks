package cli

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	wks "github.com/A1b3rt0M3rcad0/wks/packages/wks-sdk-go"
	"github.com/A1b3rt0M3rcad0/wks/packages/wksctl/internal/config"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

func uploadFile(ctx context.Context, client *wks.Client, option options, current config.Context, input io.Reader, output, stderr io.Writer, name string) int {
	file, err := os.Open(option.values["file"])
	if err != nil {
		return failure(stderr, errors.New("upload requires a readable --file"))
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		return failure(stderr, errors.New("upload requires a regular file"))
	}
	identity := option.values["id"]
	key := option.values["key"]
	if key == "" {
		key = wks.OperationKey()
	}
	if name == "source upload" {
		if current.Namespace == "" {
			return failure(stderr, errors.New("source upload requires a namespace context"))
		}
		hash := sha256.New()
		if _, err = io.Copy(hash, file); err != nil {
			return failure(stderr, err)
		}
		file.Seek(0, io.SeekStart)
		title := option.values["title"]
		if title == "" {
			title = filepath.Base(file.Name())
		}
		mime := option.values["mime"]
		if mime == "" {
			mime = "application/octet-stream"
		}
		body := map[string]any{"namespace_id": current.Namespace, "kind": "upload", "title": title, "upload": map[string]any{"filename": filepath.Base(file.Name()), "declared_media_type": mime, "byte_size": info.Size(), "checksum_sha256": hex.EncodeToString(hash.Sum(nil))}}
		if option.flags["dry-run"] {
			emit(output, map[string]any{"action": "upload", "byte_size": info.Size(), "checksum_sha256": hex.EncodeToString(hash.Sum(nil)), "mutated": false})
			return 0
		}
		var receipt wks.Receipt
		if err = client.Do(ctx, "POST", "/v1/sources", key, body, &receipt); err != nil {
			return failure(stderr, err)
		}
		identity = receipt.UploadID
		if identity == "" {
			return failure(stderr, &wks.Error{Code: "contract.response_invalid", Message: "Upload identity not confirmed", WriteOutcome: "unknown", OperationKey: key})
		}
		emit(stderr, map[string]any{"stage": "registered", "upload_id": identity, "operation_key": key})
	}
	if identity == "" {
		return failure(stderr, errors.New("resume requires --id"))
	}
	if _, err = client.ResumeUpload(ctx, identity, file, nil); err != nil {
		emit(stderr, map[string]any{"upload_id": identity, "resume": "inspect upload status; resume from its confirmed offset"})
		return failure(stderr, err)
	}
	var receipt wks.Receipt
	if err = client.Do(ctx, "POST", "/v1/uploads/"+url.PathEscape(identity)+"/commit", key+"-commit", nil, &receipt); err != nil {
		return failure(stderr, err)
	}
	emit(output, receipt)
	return 0
}
func downloadFile(ctx context.Context, client *wks.Client, option options, output, stderr io.Writer, name string) int {
	identity, destination := option.values["id"], option.values["output"]
	if identity == "" || destination == "" {
		return failure(stderr, errors.New("download requires --id and --output"))
	}
	metadata := map[string]any{}
	path := "/v1/assets/" + url.PathEscape(identity) + "/metadata"
	if strings.HasPrefix(name, "export") {
		path = "/v1/exports/" + url.PathEscape(identity)
	}
	if err := client.Do(ctx, "GET", path, "", nil, &metadata); err != nil {
		return failure(stderr, err)
	}
	sha, _ := metadata["checksum"].(string)
	if sha == "" {
		sha, _ = metadata["sha256"].(string)
	}
	content, _ := metadata["content_path"].(string)
	if content == "" {
		return failure(stderr, errors.New("content is not ready"))
	}
	if _, err := os.Lstat(destination); err == nil {
		return failure(stderr, errors.New("output already exists"))
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0700); err != nil {
		return failure(stderr, err)
	}
	file, err := os.CreateTemp(filepath.Dir(destination), ".wks-download-")
	if err != nil {
		return failure(stderr, err)
	}
	defer os.Remove(file.Name())
	file.Chmod(0600)
	count, err := client.Download(ctx, content, file, sha)
	if err != nil {
		file.Close()
		return failure(stderr, err)
	}
	if err = file.Sync(); err != nil {
		file.Close()
		return failure(stderr, err)
	}
	if err = file.Close(); err != nil {
		return failure(stderr, err)
	}
	if err = os.Rename(file.Name(), destination); err != nil {
		return failure(stderr, err)
	}
	emit(output, map[string]any{"saved": true, "byte_size": count, "sha256": sha, "path": destination})
	return 0
}
