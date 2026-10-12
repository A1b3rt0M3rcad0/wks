package cli

import (
	"context"
	"encoding/json"
	"errors"
	wks "github.com/A1b3rt0M3rcad0/wks/packages/wks-sdk-go"
	"github.com/A1b3rt0M3rcad0/wks/packages/wksctl/internal/config"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type options struct {
	words  []string
	values map[string]string
	flags  map[string]bool
}

var booleanFlags = map[string]bool{"json": true, "all": true, "yes": true, "dry-run": true, "token-stdin": true, "agents": true, "help": true}

func parse(args []string) (options, error) {
	value := options{values: map[string]string{}, flags: map[string]bool{}}
	for i := 0; i < len(args); i++ {
		argument := args[i]
		if strings.HasPrefix(argument, "--") {
			name := strings.TrimPrefix(argument, "--")
			if booleanFlags[name] {
				value.flags[name] = true
				continue
			}
			key, field, has := strings.Cut(name, "=")
			if has {
				value.values[key] = field
				continue
			}
			if i+1 >= len(args) || strings.HasPrefix(args[i+1], "--") {
				return value, errors.New("flag requires a value: " + name)
			}
			i++
			value.values[name] = args[i]
		} else {
			value.words = append(value.words, argument)
		}
	}
	return value, nil
}
func emit(out io.Writer, value any) error {
	encoder := json.NewEncoder(out)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	return encoder.Encode(value)
}
func failure(out io.Writer, err error) int {
	var api *wks.Error
	if errors.As(err, &api) {
		emit(out, api)
		if api.WriteOutcome == "unknown" || api.WriteOutcome == "reconcile_required" {
			return 3
		}
		if api.Status == 401 || api.Status == 403 {
			return 4
		}
		return 1
	}
	emit(out, map[string]any{"code": "cli.invalid", "message": err.Error(), "write_outcome": "rejected"})
	return 2
}
func Run(args []string, input io.Reader, output, stderr io.Writer, version, commit string) int {
	option, err := parse(args)
	if err != nil {
		return failure(stderr, err)
	}
	name := strings.Join(option.words, " ")
	if name == "diff" {
		name = "plan"
	}
	if name == "reconcile" {
		name = "operation receipt"
		if option.values["id"] == "" {
			option.values["id"] = option.values["key"]
		}
	}
	if name == "" || name == "help" || option.flags["help"] {
		emit(output, map[string]any{"name": "wksctl", "version": version, "commands": Commands, "local_commands": []string{"auth login --token-stdin", "auth logout", "context list", "context use", "context set", "doctor", "operation wait", "reconcile --key KEY", "upload resume", "source upload", "asset download", "export download", "init --agents", "diff", "plan", "apply --yes", "sync --yes", "test", "schema"}, "usage": "wksctl <command> --config PATH --context NAME [--id ID] [--json-body FILE|-] [--key KEY] [--all]"})
		return 0
	}
	if name == "schema" {
		output.Write(PublicSchema)
		return 0
	}
	if name == "init" {
		return initialize(option, output, stderr)
	}
	if name == "version" {
		emit(output, map[string]string{"version": version, "commit": commit})
		return 0
	}
	configPath := option.values["config"]
	if configPath == "" {
		configPath, err = config.DefaultPath()
		if err != nil {
			return failure(stderr, err)
		}
	}
	saved, err := config.Load(configPath)
	if err != nil {
		return failure(stderr, err)
	}
	selected := option.values["context"]
	if selected == "" {
		selected = saved.Current
	}
	if selected == "" {
		selected = "default"
	}
	current := saved.Contexts[selected]
	if value := option.values["endpoint"]; value != "" {
		current.Endpoint = value
	}
	if value := option.values["namespace"]; value != "" {
		current.Namespace = value
	}
	if value := option.values["grant"]; value != "" {
		current.Grant = value
	}
	if name == "context list" {
		contexts := map[string]any{}
		for key, value := range saved.Contexts {
			contexts[key] = map[string]any{"endpoint": value.Endpoint, "namespace_id": value.Namespace, "grant_configured": value.Grant != "", "authenticated": value.Token != ""}
		}
		emit(output, map[string]any{"current": saved.Current, "contexts": contexts})
		return 0
	}
	if name == "context use" || name == "context set" {
		if name == "context use" {
			if _, exists := saved.Contexts[selected]; !exists {
				return failure(stderr, errors.New("context does not exist"))
			}
		} else {
			if _, err = wks.New(current.Endpoint, ""); err != nil {
				return failure(stderr, err)
			}
			saved.Contexts[selected] = current
		}
		saved.Current = selected
		if err = config.Save(configPath, saved); err != nil {
			return failure(stderr, err)
		}
		emit(output, map[string]any{"context": selected, "saved": true})
		return 0
	}
	if name == "auth logout" {
		current.Token = ""
		saved.Contexts[selected] = current
		if err = config.Save(configPath, saved); err != nil {
			return failure(stderr, err)
		}
		emit(output, map[string]any{"context": selected, "authenticated": false})
		return 0
	}
	if name == "auth login" {
		if !option.flags["token-stdin"] {
			return failure(stderr, errors.New("use --token-stdin; credentials must not be passed in arguments"))
		}
		raw, e := io.ReadAll(io.LimitReader(input, 8193))
		if e != nil || len(raw) > 8192 {
			return failure(stderr, errors.New("credential input exceeds limit"))
		}
		current.Token = strings.TrimSpace(string(raw))
		if current.Token == "" || strings.ContainsAny(current.Token, "\r\n\t ") {
			return failure(stderr, errors.New("one credential is required on stdin"))
		}
	}
	client, err := wks.New(current.Endpoint, current.Token)
	if err != nil {
		return failure(stderr, err)
	}
	client.Grant = current.Grant
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	if name == "auth login" {
		var value map[string]any
		if err = client.Do(ctx, "GET", "/v1/capabilities", "", nil, &value); err != nil {
			return failure(stderr, err)
		}
		saved.Current = selected
		saved.Contexts[selected] = current
		if err = config.Save(configPath, saved); err != nil {
			return failure(stderr, err)
		}
		emit(output, map[string]any{"context": selected, "authenticated": true})
		return 0
	}
	if name == "doctor" {
		var ready, capability map[string]any
		if err = client.Do(ctx, "GET", "/health/ready", "", nil, &ready); err != nil {
			return failure(stderr, err)
		}
		if err = client.Do(ctx, "GET", "/v1/capabilities", "", nil, &capability); err != nil {
			return failure(stderr, err)
		}
		emit(output, map[string]any{"health": ready, "capabilities": capability})
		return 0
	}
	if name == "schema" {
		var value map[string]any
		if err = client.Do(ctx, "GET", "/openapi.json", "", nil, &value); err != nil {
			return failure(stderr, err)
		}
		emit(output, value)
		return 0
	}
	if name == "operation wait" {
		value, e := client.WaitOperation(ctx, option.values["id"])
		if e != nil {
			return failure(stderr, e)
		}
		emit(output, value)
		return 0
	}
	if name == "upload resume" || name == "source upload" {
		return uploadFile(ctx, client, option, current, input, output, stderr, name)
	}
	if name == "asset download" || name == "export download" {
		return downloadFile(ctx, client, option, output, stderr, name)
	}
	if name == "plan" || name == "apply" || name == "sync" {
		return manifestRun(ctx, client, option, current, output, stderr, name)
	}
	if name == "test" {
		return testRun(ctx, client, option, output, stderr)
	}
	command, exists := Find(name)
	if !exists {
		return failure(stderr, errors.New("unknown command; use help --json"))
	}
	identity := option.values["id"]
	namespace := current.Namespace
	path := strings.ReplaceAll(command.Path, "{id}", url.PathEscape(identity))
	path = strings.ReplaceAll(path, "{namespace}", url.PathEscape(namespace))
	path = strings.ReplaceAll(path, "{source}", url.PathEscape(option.values["source"]))
	path = strings.ReplaceAll(path, "{block}", url.PathEscape(option.values["block"]))
	if strings.Contains(command.Path, "{id}") && identity == "" || strings.Contains(command.Path, "{namespace}") && namespace == "" || strings.Contains(command.Path, "{source}") && option.values["source"] == "" || strings.Contains(command.Path, "{block}") && option.values["block"] == "" {
		return failure(stderr, errors.New("command requires its resource identifiers; use --id/--namespace/--source/--block"))
	}
	body, err := bodyFor(command, option, current, input)
	if err != nil {
		return failure(stderr, err)
	}
	if command.Mutation && option.flags["dry-run"] {
		emit(output, map[string]any{"command": command.Name, "method": command.Method, "path": path, "mutated": false, "destructive": command.Destructive})
		return 0
	}
	headers := map[string]string{}
	if value := option.values["if-match"]; value != "" {
		headers["If-Match"] = value
	}
	if command.Destructive && !option.flags["yes"] {
		return failure(stderr, errors.New("inspect the impact and pass --yes to perform this destructive action"))
	}
	if name == "source delete" || name == "source revoke" || name == "namespace delete" || name == "collection delete" {
		token := option.values["confirmation"]
		if token == "" {
			var report map[string]any
			action := "delete"
			if name == "source revoke" {
				action = "revoke"
			}
			endpoint := "/v1/sources/" + url.PathEscape(identity) + "/preflight?action=" + action
			if name == "namespace delete" {
				endpoint = "/v1/namespaces/" + url.PathEscape(identity) + "/preflight"
			}
			if name == "collection delete" {
				endpoint = "/v1/collections/" + url.PathEscape(identity) + "/preflight"
			}
			if err = client.Do(ctx, "POST", endpoint, "", nil, &report); err != nil {
				return failure(stderr, err)
			}
			token, _ = report["confirmation_token"].(string)
		}
		headers["X-WKS-Confirmation"] = token
	}
	key := option.values["key"]
	if command.Mutation && key == "" {
		key = wks.OperationKey()
	}
	parameters := url.Values{}
	if command.Method == "GET" {
		for _, field := range []string{"cursor", "page_size", "offset", "page_chars"} {
			flag := strings.ReplaceAll(field, "_", "-")
			if value := option.values[flag]; value != "" {
				parameters.Set(field, value)
			}
		}
		if name == "source list" && namespace != "" {
			parameters.Set("namespace_id", namespace)
		}
		if len(parameters) > 0 {
			path += "?" + parameters.Encode()
		}
	}
	pages := []map[string]any{}
	seenCursors := map[string]bool{}
	pageCount := 0
	for {
		value := map[string]any{}
		if err = client.DoHeaders(ctx, command.Method, path, key, body, headers, &value); err != nil {
			return failure(stderr, err)
		}
		if name == "credential create" {
			secret, _ := value["token"].(string)
			delete(value, "token")
			if secret != "" {
				if option.values["token-file"] == "" {
					return failure(stderr, errors.New("credential issuance requires --token-file"))
				}
				if err = privateFile(option.values["token-file"], []byte(secret)); err != nil {
					return failure(stderr, err)
				}
			}
		}
		if option.flags["all"] {
			json.NewEncoder(output).Encode(value)
		} else {
			pages = append(pages, value)
		}
		cursor, _ := value["next_cursor"].(string)
		if !option.flags["all"] || cursor == "" {
			break
		}
		pageCount++
		if seenCursors[cursor] || pageCount > 100000 {
			return failure(stderr, errors.New("pagination cycle or safety budget exhausted"))
		}
		seenCursors[cursor] = true
		if command.Method == "GET" {
			parsed, _ := url.Parse(path)
			query := parsed.Query()
			query.Set("cursor", cursor)
			parsed.RawQuery = query.Encode()
			path = parsed.String()
		} else {
			body["cursor"] = cursor
		}
	}
	if option.flags["all"] {
		return 0
	}
	if len(pages) == 1 {
		emit(output, pages[0])
	} else {
		emit(output, map[string]any{"pages": pages, "complete": true})
	}
	return 0
}

func bodyFor(command Command, option options, current config.Context, input io.Reader) (map[string]any, error) {
	body := map[string]any{}
	if file := option.values["json-body"]; file != "" {
		var raw []byte
		var e error
		if file == "-" {
			raw, e = io.ReadAll(io.LimitReader(input, 2<<20+1))
		} else {
			raw, e = os.ReadFile(file)
		}
		if e != nil || len(raw) > 2<<20 {
			return nil, errors.New("JSON input exceeds limit or cannot be read")
		}
		if e = json.Unmarshal(raw, &body); e != nil || body == nil {
			return nil, errors.New("invalid JSON input")
		}
	}
	for _, field := range []string{"title", "description", "kind", "name", "role", "audience", "purpose", "format", "state"} {
		if value := option.values[field]; value != "" {
			body[field] = value
		}
	}
	switch command.Name {
	case "namespace create":
		if body["title"] == nil {
			body["title"] = "Workspace"
		}
	case "collection create":
		body["namespace_id"] = current.Namespace
	case "source create":
		body["namespace_id"] = current.Namespace
		if body["kind"] == nil {
			body["kind"] = "text"
		}
		if body["title"] == nil {
			body["title"] = "Source"
		}
		if value := option.values["text"]; value != "" {
			body["text"] = value
		}
		if file := option.values["file"]; file != "" {
			raw, e := os.ReadFile(file)
			if e != nil {
				return nil, e
			}
			body["text"] = string(raw)
		}
		if value := option.values["uri"]; value != "" {
			body["external_uri"] = value
			body["kind"] = "bookmark"
		}
	case "source version":
		if value := option.values["text"]; value != "" {
			body["text"] = value
		}
	case "search", "search suggest":
		if value := option.values["query"]; value != "" {
			body["query"] = value
		}
		for _, field := range []string{"mode", "language", "cursor"} {
			if value := option.values[field]; value != "" {
				body[field] = value
			}
		}
		if body["query"] == nil {
			return nil, errors.New("search requires --query")
		}
		if current.Namespace != "" {
			if body["filters"] == nil {
				body["filters"] = map[string]any{}
			}
			filters, ok := body["filters"].(map[string]any)
			if !ok {
				return nil, errors.New("filters must be an object")
			}
			filters["namespace_id"] = current.Namespace
		}
	case "grant create":
		if current.Namespace != "" && body["namespace_id"] == nil && body["source_version_ids"] == nil {
			body["namespace_id"] = current.Namespace
		}
		if option.values["caller"] != "" {
			body["caller_binding"] = option.values["caller"]
		}
		if option.values["actions"] != "" {
			body["allowed_actions"] = strings.Split(option.values["actions"], ",")
		}
		if body["purpose"] == nil {
			body["purpose"] = "integration"
		}
		if body["audience"] == nil {
			body["audience"] = "wks"
		}
		if value := option.values["collection"]; value != "" {
			body["collection_id"] = value
		}
	case "credential create":
		if _, err := os.Lstat(option.values["token-file"]); err == nil {
			return nil, errors.New("credential output already exists; no credential issued")
		}
		if option.values["token-file"] == "" {
			return nil, errors.New("credential issuance requires --token-file before the request")
		}
	}
	for flag, field := range map[string]string{"ttl": "ttl_seconds", "distance": "distance", "page-size": "page_size"} {
		if value := option.values[flag]; value != "" {
			parsed, e := strconv.Atoi(value)
			if e != nil {
				return nil, errors.New("invalid integer option")
			}
			body[field] = parsed
		}
	}
	return body, nil
}
func privateFile(path string, raw []byte) error {
	if path == "" {
		return errors.New("output path is required")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return errors.New("private output already exists or cannot be created")
	}
	defer file.Close()
	if err = config.SecurePrivate(path); err != nil {
		os.Remove(path)
		return err
	}
	if _, err = file.Write(raw); err != nil {
		return err
	}
	return file.Sync()
}
