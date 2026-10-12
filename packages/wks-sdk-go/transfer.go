package wks

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/url"
)

type Upload struct {
	ID           string `json:"upload_id"`
	Status       string `json:"status"`
	Offset       int64  `json:"offset"`
	ExpectedSize int64  `json:"expected_size"`
}

func (c *Client) UploadStatus(ctx context.Context, id string) (Upload, error) {
	var value Upload
	e := c.Do(ctx, "GET", "/v1/uploads/"+url.PathEscape(id), "", nil, &value)
	return value, e
}

// ResumeUpload seeks to the server-confirmed offset and verifies every chunk independently.
// Retrying is always a caller decision after consulting UploadStatus; no blind write retry occurs.
func (c *Client) ResumeUpload(ctx context.Context, id string, source io.ReadSeeker, progress func(int64, int64)) (Upload, error) {
	status, e := c.UploadStatus(ctx, id)
	if e != nil {
		return status, e
	}
	if status.Offset < 0 || status.Offset > status.ExpectedSize {
		return status, errors.New("invalid server upload offset")
	}
	if _, e = source.Seek(status.Offset, io.SeekStart); e != nil {
		return status, e
	}
	buffer := make([]byte, 4<<20)
	for status.Offset < status.ExpectedSize {
		remaining := status.ExpectedSize - status.Offset
		size := int64(len(buffer))
		if remaining < size {
			size = remaining
		}
		n, e := io.ReadFull(source, buffer[:size])
		if e != nil {
			return status, e
		}
		digest := sha256.Sum256(buffer[:n])
		path := "/v1/uploads/" + url.PathEscape(id) + "/parts?offset=" + itoa(status.Offset) + "&checksum_sha256=" + hex.EncodeToString(digest[:])
		response, e := c.Request(ctx, "PUT", path, "", &byteReader{data: buffer[:n]}, "application/octet-stream")
		if e != nil {
			return status, e
		}
		e = json.NewDecoder(io.LimitReader(response.Body, 65536)).Decode(&status)
		response.Body.Close()
		if e != nil {
			return status, e
		}
		if progress != nil {
			progress(status.Offset, status.ExpectedSize)
		}
	}
	return status, nil
}

type byteReader struct{ data []byte }

func (b *byteReader) Read(out []byte) (int, error) {
	if len(b.data) == 0 {
		return 0, io.EOF
	}
	n := copy(out, b.data)
	b.data = b.data[n:]
	return n, nil
}
func itoa(value int64) string {
	if value == 0 {
		return "0"
	}
	b := make([]byte, 0, 20)
	for value > 0 {
		b = append(b, byte('0'+value%10))
		value /= 10
	}
	for i, j := 0, len(b)-1; i < j; i, j = i+1, j-1 {
		b[i], b[j] = b[j], b[i]
	}
	return string(b)
}

// Download streams to a writer and verifies the published SHA-256 when provided.
func (c *Client) Download(ctx context.Context, path string, destination io.Writer, expectedSHA256 string) (int64, error) {
	response, e := c.Request(ctx, "GET", path, "", nil, "")
	if e != nil {
		return 0, e
	}
	defer response.Body.Close()
	hash := sha256.New()
	count, e := io.Copy(io.MultiWriter(destination, hash), response.Body)
	if e != nil {
		return count, e
	}
	if expectedSHA256 != "" && hex.EncodeToString(hash.Sum(nil)) != expectedSHA256 {
		return count, &Error{Code: "asset.checksum_mismatch", Message: "Downloaded content failed integrity validation"}
	}
	return count, nil
}
func (c *Client) EachRead(ctx context.Context, id string, consume func(Read) error) error {
	cursor := ""
	seen := map[string]bool{}
	for {
		page, e := c.Read(ctx, id, cursor)
		if e != nil {
			return e
		}
		if e = consume(page); e != nil {
			return e
		}
		if page.NextCursor == nil {
			return nil
		}
		cursor = *page.NextCursor
		if seen[cursor] {
			return errors.New("repeated pagination cursor")
		}
		seen[cursor] = true
	}
}
