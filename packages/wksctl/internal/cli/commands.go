package cli

type Command struct {
	Name        string `json:"name"`
	Method      string `json:"method"`
	Path        string `json:"path"`
	Summary     string `json:"summary"`
	Mutation    bool   `json:"mutation"`
	Destructive bool   `json:"destructive"`
}

var Commands = []Command{
	{"source reprocess", "POST", "/v1/namespaces/{namespace}/reprocess", "Plan or schedule bounded filtered reprocessing; JSON body dry_run defaults true", true, false},
	{"source versions", "GET", "/v1/sources/{id}/versions", "Page immutable source revisions", false, false},
	{"client me", "GET", "/v1/clients/me", "Read the authenticated integration identity", false, false},
	{"credential list", "GET", "/v1/clients/{id}/credentials", "List public credential identities", false, false},
	{"namespace preflight", "POST", "/v1/namespaces/{id}/preflight", "Inspect deletion of an empty namespace", false, false},
	{"namespace delete", "DELETE", "/v1/namespaces/{id}", "Delete an empty namespace with confirmation", true, true},
	{"collection preflight", "POST", "/v1/collections/{id}/preflight", "Inspect removal of collection links", false, false},
	{"collection delete", "DELETE", "/v1/collections/{id}", "Delete a collection while keeping source bytes", true, true},
	{"representation get", "GET", "/v1/representations/{id}", "Read immutable producer and coverage metadata", false, false},
	{"source deletion", "GET", "/v1/sources/{id}/deletion", "Inspect physical cleanup after revocation", false, false},
	{"worker status", "GET", "/v1/workers/status", "Inspect scoped worker liveness, backlog, retries, parser and coverage", false, false},
	{"capabilities", "GET", "/v1/capabilities", "Inspect live formats, budgets and worker availability", false, false},
	{"namespace list", "GET", "/v1/namespaces", "List owned or granted namespaces", false, false},
	{"namespace get", "GET", "/v1/namespaces/{id}", "Read namespace metadata and revision", false, false},
	{"namespace create", "POST", "/v1/namespaces", "Create a namespace", true, false},
	{"namespace update", "PATCH", "/v1/namespaces/{id}", "Rename or archive with If-Match", true, false},
	{"collection list", "GET", "/v1/namespaces/{namespace}/collections", "List scoped collections", false, false},
	{"collection get", "GET", "/v1/collections/{id}", "Read collection metadata", false, false},
	{"collection create", "POST", "/v1/collections", "Create a collection", true, false},
	{"collection update", "PATCH", "/v1/collections/{id}", "Rename or archive with If-Match", true, false},
	{"collection link", "PUT", "/v1/collections/{id}/sources/{source}", "Link an existing source", true, false},
	{"collection unlink", "DELETE", "/v1/collections/{id}/sources/{source}", "Remove one collection link", true, true},
	{"source list", "GET", "/v1/sources", "List scoped sources", false, false},
	{"source get", "GET", "/v1/sources/{id}", "Read source and immutable version identities", false, false},
	{"source history", "GET", "/v1/sources/{id}/history", "List historical representations and processing attempts", false, false},
	{"source create", "POST", "/v1/sources", "Register text, bookmark or an out-of-band upload session", true, false},
	{"source version", "POST", "/v1/sources/{id}/versions", "Create an explicit immutable revision", true, false},
	{"source update", "PATCH", "/v1/sources/{id}", "Update metadata with If-Match", true, false},
	{"source process", "POST", "/v1/sources/{id}/process", "Request a processing attempt", true, false},
	{"source capture", "POST", "/v1/sources/{id}/capture", "Capture an explicitly allowed public bookmark", true, false},
	{"source preflight", "POST", "/v1/sources/{id}/preflight", "Inspect destructive impact before confirming", false, false},
	{"source revoke", "POST", "/v1/sources/{id}/revoke", "Revoke access using a preflight confirmation", true, true},
	{"source delete", "DELETE", "/v1/sources/{id}", "Purge content using a preflight confirmation", true, true},
	{"upload status", "GET", "/v1/uploads/{id}", "Inspect the confirmed upload offset", false, false},
	{"upload commit", "POST", "/v1/uploads/{id}/commit", "Verify and commit a complete upload", true, false},
	{"upload abort", "POST", "/v1/uploads/{id}/abort", "Cancel an unfinished upload", true, true},
	{"operation get", "GET", "/v1/operations/{id}", "Inspect confirmed processing state", false, false},
	{"operation receipt", "GET", "/v1/receipts/{id}", "Recover an ambiguous write by its operation key", false, false},
	{"search", "POST", "/v1/search", "Run scoped lexical, phrase, exact, fuzzy or proximity search", false, false},
	{"search suggest", "POST", "/v1/search/suggestions", "Suggest authorized lexical candidates", false, false},
	{"read", "GET", "/v1/representations/{id}/read", "Read an exact representation with explicit pagination", false, false},
	{"block", "GET", "/v1/representations/{id}/blocks/{block}", "Resolve a full canonical block in bounded JSON pages", false, false},
	{"asset metadata", "GET", "/v1/assets/{id}/metadata", "Inspect an authorized asset", false, false},
	{"export create", "POST", "/v1/representations/{id}/exports", "Schedule an immutable full export", true, false},
	{"export get", "GET", "/v1/exports/{id}", "Inspect export state and SHA-256", false, false},
	{"grant list", "GET", "/v1/grants", "List issuer-owned delegations", false, false},
	{"grant create", "POST", "/v1/grants", "Create an action-limited expiring delegation", true, false},
	{"grant revoke", "POST", "/v1/grants/{id}/revoke", "Revoke an owned delegation", true, true},
	{"events", "GET", "/v1/namespaces/{namespace}/events", "Replay authorized events by cursor", false, false},
	{"client create", "POST", "/v1/clients", "Provision an integration identity as the human operator", true, false},
	{"credential create", "POST", "/v1/clients/{id}/credentials", "Issue a secret once into a private file", true, false},
	{"credential revoke", "POST", "/v1/credentials/{id}/revoke", "Revoke a credential and in-flight access", true, true},
}

func Find(name string) (Command, bool) {
	for _, command := range Commands {
		if command.Name == name {
			return command, true
		}
	}
	return Command{}, false
}
