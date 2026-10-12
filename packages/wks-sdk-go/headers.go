package wks

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
)

// DoHeaders applies optimistic revision and preflight headers without exposing credentials.
func (c *Client) DoHeaders(ctx context.Context, method, path, key string, body any, headers map[string]string, result any) error {
	var input io.Reader
	if body != nil {
		raw, e := json.Marshal(body)
		if e != nil {
			return e
		}
		input = bytes.NewReader(raw)
	}
	response, e := c.requestHeaders(ctx, method, path, key, input, "application/json", headers)
	if e != nil {
		return e
	}
	defer response.Body.Close()
	if result == nil {
		_, e = io.Copy(io.Discard, response.Body)
		return e
	}
	e = json.NewDecoder(io.LimitReader(response.Body, 8<<20)).Decode(result)
	if e != nil {
		return &Error{Code: "transport.response_invalid", Message: "Response could not be confirmed", WriteOutcome: "unknown", OperationKey: key, Retryable: true}
	}
	return nil
}
