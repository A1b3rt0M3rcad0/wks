package cli

import (
	"bytes"
	"context"
	"errors"
	wks "github.com/A1b3rt0M3rcad0/wks/packages/wks-sdk-go"
	"gopkg.in/yaml.v3"
	"io"
	"os"
	"strings"
)

type TestFixture struct {
	Name  string `yaml:"name"`
	Title string `yaml:"title"`
	Text  string `yaml:"text"`
}
type TestQuery struct {
	Name            string   `yaml:"name"`
	Query           string   `yaml:"query"`
	Mode            string   `yaml:"mode"`
	Language        string   `yaml:"language"`
	ExpectedSources []string `yaml:"expected_sources"`
	Contains        string   `yaml:"contains"`
}
type TestSuite struct {
	SchemaVersion int           `yaml:"schema_version"`
	Fixtures      []TestFixture `yaml:"fixtures"`
	Queries       []TestQuery   `yaml:"queries"`
}

func testRun(ctx context.Context, client *wks.Client, option options, output, stderr io.Writer) int {
	path := option.values["tests"]
	if path == "" {
		path = ".wks/tests.yaml"
	}
	raw, e := os.ReadFile(path)
	if e != nil || len(raw) > 2<<20 {
		return failure(stderr, errors.New("test suite cannot be read or exceeds limit"))
	}
	decoder := yaml.NewDecoder(bytes.NewReader(raw))
	decoder.KnownFields(true)
	var suite TestSuite
	if e = decoder.Decode(&suite); e != nil || suite.SchemaVersion != 1 || len(suite.Fixtures) == 0 || len(suite.Queries) == 0 {
		return failure(stderr, errors.New("test suite requires schema_version 1, fixtures and explicit query assertions"))
	}
	names := map[string]bool{}
	for _, fixture := range suite.Fixtures {
		if fixture.Name == "" || names[fixture.Name] || fixture.Text == "" {
			return failure(stderr, errors.New("test fixtures require unique names and source text"))
		}
		names[fixture.Name] = true
	}
	for _, query := range suite.Queries {
		if query.Query == "" || len(query.ExpectedSources) == 0 && query.Contains == "" {
			return failure(stderr, errors.New("every query requires an explicit assertion"))
		}
		for _, name := range query.ExpectedSources {
			if !names[name] {
				return failure(stderr, errors.New("assertion references an unknown fixture"))
			}
		}
	}
	namespace, e := client.CreateNamespace(ctx, "wksctl-isolated-test", wks.OperationKey())
	if e != nil {
		return failure(stderr, e)
	}
	ids := map[string]string{}
	for _, fixture := range suite.Fixtures {
		title := fixture.Title
		if title == "" {
			title = fixture.Name
		}
		receipt, e := client.CreateText(ctx, namespace.ID, title, fixture.Text, wks.OperationKey())
		if e != nil {
			return failure(stderr, e)
		}
		ids[fixture.Name] = receipt.SourceID
		if _, e = client.WaitOperation(ctx, receipt.OperationID); e != nil {
			return failure(stderr, e)
		}
	}
	results := []map[string]any{}
	passed := 0
	for _, query := range suite.Queries {
		body := map[string]any{"query": query.Query, "mode": "lexical", "language": "auto", "page_size": 50, "filters": map[string]any{"namespace_id": namespace.ID}}
		if query.Mode != "" {
			body["mode"] = query.Mode
		}
		if query.Language != "" {
			body["language"] = query.Language
		}
		var result wks.Search
		e = client.Do(ctx, "POST", "/v1/search", "", body, &result)
		if e != nil {
			return failure(stderr, e)
		}
		hits := map[string]bool{}
		text := ""
		for _, hit := range result.Items {
			hits[hit.SourceID] = true
			text += hit.Snippet
		}
		success := true
		for _, name := range query.ExpectedSources {
			if !hits[ids[name]] {
				success = false
			}
		}
		if query.Contains != "" && !strings.Contains(text, query.Contains) {
			success = false
		}
		if success {
			passed++
		}
		results = append(results, map[string]any{"name": query.Name, "passed": success, "complete": result.NextCursor == nil})
	}
	emit(output, map[string]any{"schema_version": 1, "namespace_id": namespace.ID, "isolated": true, "passed": passed, "failed": len(results) - passed, "queries": results, "source_count": len(ids), "qualification": "supplied assertions only"})
	if passed != len(results) {
		return 1
	}
	return 0
}
