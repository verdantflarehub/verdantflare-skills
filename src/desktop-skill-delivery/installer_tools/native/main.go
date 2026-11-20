// Engineering-only native resource verifier. Publisher signing, user installation
// and desktop readiness are separate requirements; none is inferred here.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
)

var manifestSHA256 string // injected from independently checked build input
var hashPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)
var partPattern = regexp.MustCompile(`^[A-Za-z0-9_.+ -]+$`)

const maxManifest = 8 * 1024 * 1024
const maxFile = 256 * 1024 * 1024
const maxTotal = 2 * 1024 * 1024 * 1024

type entry struct {
	Type   string  `json:"type"`
	Size   *int64  `json:"size,omitempty"`
	SHA256 string  `json:"sha256,omitempty"`
	Mode   *uint32 `json:"mode,omitempty"`
	Target string  `json:"target,omitempty"`
}
type manifest struct {
	Schema   string           `json:"schema"`
	Platform string           `json:"platform"`
	Python   string           `json:"python"`
	Driver   string           `json:"driver"`
	Entries  map[string]entry `json:"entries"`
}

func reject(code string) error { return errors.New(code) }
func digest(raw []byte) string { sum := sha256.Sum256(raw); return hex.EncodeToString(sum[:]) }
func hostPlatform() string {
	if runtime.GOOS == "darwin" && runtime.GOARCH == "arm64" {
		return "macos-arm64"
	}
	if runtime.GOOS == "windows" && runtime.GOARCH == "amd64" {
		return "windows-amd64"
	}
	return "unsupported"
}
func safePath(name string) bool {
	if len(name) == 0 || len(name) > 1024 || strings.Contains(name, "\\") {
		return false
	}
	for _, p := range strings.Split(name, "/") {
		if len(p) == 0 || len(p) > 255 || p == "." || p == ".." || strings.TrimSpace(p) != p || strings.HasSuffix(p, ".") || !partPattern.MatchString(p) {
			return false
		}
		stem := strings.ToLower(strings.SplitN(p, ".", 2)[0])
		if stem == "con" || stem == "prn" || stem == "aux" || stem == "nul" || len(stem) == 4 && (strings.HasPrefix(stem, "com") || strings.HasPrefix(stem, "lpt")) && stem[3] >= '1' && stem[3] <= '9' {
			return false
		}
	}
	return true
}
func noLinks(name string) error {
	absolute, e := filepath.Abs(name)
	if e != nil {
		return reject("ROOT_INVALID")
	}
	for current := absolute; ; current = filepath.Dir(current) {
		info, e := os.Lstat(current)
		if e != nil || info.Mode()&os.ModeSymlink != 0 {
			return reject("ROOT_LINK_OR_MISSING")
		}
		if filepath.Dir(current) == current {
			break
		}
	}
	return nil
}

// encoding/json's struct decoder alone silently accepts duplicate keys. Walk the
// token stream first, with bounded nesting, then decode the fixed schema.
func uniqueJSON(raw []byte) error {
	d := json.NewDecoder(strings.NewReader(string(raw)))
	d.UseNumber()
	var value func(int) error
	value = func(depth int) error {
		if depth > 64 {
			return reject("MANIFEST_TOO_DEEP")
		}
		token, e := d.Token()
		if e != nil {
			return reject("MANIFEST_JSON_INVALID")
		}
		delim, ok := token.(json.Delim)
		if !ok {
			return nil
		}
		switch delim {
		case '{':
			seen := map[string]bool{}
			for d.More() {
				t, e := d.Token()
				key, ok := t.(string)
				if e != nil || !ok || seen[key] {
					return reject("MANIFEST_DUPLICATE_OR_INVALID_KEY")
				}
				seen[key] = true
				if e = value(depth + 1); e != nil {
					return e
				}
			}
			close, e := d.Token()
			if e != nil || close != json.Delim('}') {
				return reject("MANIFEST_JSON_INVALID")
			}
		case '[':
			for d.More() {
				if e := value(depth + 1); e != nil {
					return e
				}
			}
			close, e := d.Token()
			if e != nil || close != json.Delim(']') {
				return reject("MANIFEST_JSON_INVALID")
			}
		default:
			return reject("MANIFEST_JSON_INVALID")
		}
		return nil
	}
	if e := value(0); e != nil {
		return e
	}
	if _, e := d.Token(); e != io.EOF {
		return reject("MANIFEST_TRAILING_CONTENT")
	}
	return nil
}
func decodeManifest(raw []byte, expected string) (manifest, error) {
	var m manifest
	if !hashPattern.MatchString(expected) || len(raw) > maxManifest || digest(raw) != expected {
		return m, reject("MANIFEST_HASH_MISMATCH")
	}
	if e := uniqueJSON(raw); e != nil {
		return m, e
	}
	d := json.NewDecoder(strings.NewReader(string(raw)))
	d.DisallowUnknownFields()
	if e := d.Decode(&m); e != nil {
		return m, reject("MANIFEST_SCHEMA_INVALID")
	}
	var shapes map[string]json.RawMessage
	if json.Unmarshal(raw, &shapes) != nil || len(shapes) != 5 {
		return m, reject("MANIFEST_SCHEMA_INVALID")
	}
	var entries map[string]map[string]json.RawMessage
	if json.Unmarshal(shapes["entries"], &entries) != nil {
		return m, reject("MANIFEST_SCHEMA_INVALID")
	}
	for name, item := range m.Entries {
		shape := entries[name]
		if item.Type == "file" {
			if len(shape) != 4 || shape["type"] == nil || shape["size"] == nil || shape["sha256"] == nil || shape["mode"] == nil {
				return m, reject("MANIFEST_FILE_INVALID")
			}
		} else if item.Type == "symlink" {
			if len(shape) != 2 || shape["type"] == nil || shape["target"] == nil {
				return m, reject("MANIFEST_LINK_INVALID")
			}
		}
	}
	if m.Schema != "vf.installer-bootstrap/v1" || m.Platform != hostPlatform() || m.Platform == "unsupported" || len(m.Entries) == 0 || len(m.Entries) > 20000 {
		return m, reject("MANIFEST_PLATFORM_OR_SCHEMA")
	}
	expectedPython := "resources/runtime/python/bin/python3.12"
	if m.Platform == "windows-amd64" {
		expectedPython = "resources/runtime/python/python.exe"
	}
	if m.Python != expectedPython || m.Driver != "resources/installer/driver.py" {
		return m, reject("MANIFEST_ENTRYPOINT_INVALID")
	}
	prefixes := map[string]string{}
	var total int64
	for name, item := range m.Entries {
		if !safePath(name) || !strings.HasPrefix(name, "resources/") {
			return m, reject("MANIFEST_PATH_INVALID")
		}
		parts := strings.Split(name, "/")
		for i := 1; i <= len(parts); i++ {
			prefix := strings.Join(parts[:i], "/")
			fold := strings.ToLower(prefix)
			if old, ok := prefixes[fold]; ok && old != prefix {
				return m, reject("MANIFEST_PATH_ALIAS")
			}
			prefixes[fold] = prefix
			if i < len(parts) {
				if _, ok := m.Entries[prefix]; ok {
					return m, reject("MANIFEST_PATH_PREFIX_COLLISION")
				}
			}
		}
		switch item.Type {
		case "file":
			if item.Size == nil || *item.Size < 0 || *item.Size > maxFile || !hashPattern.MatchString(item.SHA256) || item.Mode == nil || (*item.Mode != 0644 && *item.Mode != 0755) || item.Target != "" {
				return m, reject("MANIFEST_FILE_INVALID")
			}
			total += *item.Size
			if total > maxTotal {
				return m, reject("MANIFEST_TOO_LARGE")
			}
		case "symlink":
			if item.Size != nil || item.SHA256 != "" || item.Mode != nil || item.Target == "" || strings.HasPrefix(item.Target, "/") || strings.Contains(item.Target, "\\") {
				return m, reject("MANIFEST_LINK_INVALID")
			}
			for _, part := range strings.Split(item.Target, "/") {
				if part != "." && part != ".." && !safePath(part) {
					return m, reject("MANIFEST_LINK_INVALID")
				}
			}
		default:
			return m, reject("MANIFEST_TYPE_INVALID")
		}
	}
	for name, item := range m.Entries {
		if item.Type != "symlink" {
			continue
		}
		seen := map[string]bool{name: true}
		current := name
		for steps := 0; ; steps++ {
			if steps >= 64 {
				return m, reject("MANIFEST_LINK_CYCLE_OR_ESCAPE")
			}
			target := m.Entries[current].Target
			parts := strings.Split(path.Dir(current), "/")
			if parts[0] == "." {
				parts = nil
			}
			targetParts := strings.Split(target, "/")
			for i, p := range targetParts {
				if p == ".." {
					if len(parts) == 0 {
						return m, reject("MANIFEST_LINK_CYCLE_OR_ESCAPE")
					}
					parts = parts[:len(parts)-1]
				} else if p != "." {
					parts = append(parts, p)
				}
				if i < len(targetParts)-1 {
					if _, ok := m.Entries[strings.Join(parts, "/")]; ok {
						return m, reject("MANIFEST_LINK_PARENT_NOT_DIRECTORY")
					}
				}
			}
			destination := strings.Join(parts, "/")
			next, ok := m.Entries[destination]
			if !ok || seen[destination] {
				return m, reject("MANIFEST_LINK_CYCLE_OR_ESCAPE")
			}
			if next.Type == "file" {
				break
			}
			seen[destination] = true
			current = destination
		}
	}
	for _, name := range []string{m.Python, m.Driver} {
		item, ok := m.Entries[name]
		if !ok || item.Type != "file" || name == m.Python && *item.Mode != 0755 {
			return m, reject("MANIFEST_ENTRYPOINT_INVALID")
		}
	}
	return m, nil
}
func readBounded(name string, limit int64) ([]byte, error) {
	info, e := os.Lstat(name)
	if e != nil || !info.Mode().IsRegular() || info.Size() > limit || !singleOwnedFile(info) {
		return nil, reject("RESOURCE_FILE_INVALID")
	}
	f, e := os.Open(name)
	if e != nil {
		return nil, reject("RESOURCE_READ_FAILED")
	}
	defer f.Close()
	opened, e := f.Stat()
	if e != nil || !os.SameFile(info, opened) {
		return nil, reject("RESOURCE_CHANGED")
	}
	raw, e := io.ReadAll(io.LimitReader(f, limit+1))
	if e != nil || int64(len(raw)) > limit {
		return nil, reject("RESOURCE_READ_FAILED")
	}
	after, e := os.Lstat(name)
	if e != nil || !os.SameFile(info, after) || after.Size() != info.Size() || after.Mode() != info.Mode() {
		return nil, reject("RESOURCE_CHANGED")
	}
	return raw, nil
}
func verify(root, binaryName, expected string) (manifest, error) {
	var empty manifest
	if e := noLinks(root); e != nil {
		return empty, e
	}
	raw, e := readBounded(filepath.Join(root, "bootstrap-manifest.json"), maxManifest)
	if e != nil {
		return empty, e
	}
	m, e := decodeManifest(raw, expected)
	if e != nil {
		return empty, e
	}
	dirs := map[string]bool{".": true}
	for name := range m.Entries {
		for parent := path.Dir(name); parent != "."; parent = path.Dir(parent) {
			dirs[parent] = true
		}
	}
	seen := map[string]bool{}
	e = filepath.WalkDir(root, func(name string, d fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return reject("RESOURCE_ENUMERATION_FAILED")
		}
		rel, e := filepath.Rel(root, name)
		if e != nil {
			return reject("RESOURCE_PATH_INVALID")
		}
		rel = filepath.ToSlash(rel)
		info, e := os.Lstat(name)
		if e != nil {
			return reject("RESOURCE_READ_FAILED")
		}
		if d.IsDir() {
			if !dirs[rel] || !ownedDirectory(info) {
				return reject("RESOURCE_DIRECTORY_INVALID")
			}
			return nil
		}
		if rel == binaryName || rel == "bootstrap-manifest.json" {
			if !info.Mode().IsRegular() || !singleOwnedFile(info) {
				return reject("BOOTSTRAP_FILE_INVALID")
			}
			return nil
		}
		item, ok := m.Entries[rel]
		if !ok {
			return reject("RESOURCE_UNKNOWN_MEMBER")
		}
		seen[rel] = true
		if item.Type == "symlink" {
			target, e := os.Readlink(name)
			if e != nil || target != item.Target {
				return reject("RESOURCE_LINK_CHANGED")
			}
			return nil
		}
		if !info.Mode().IsRegular() || !singleOwnedFile(info) || info.Size() != *item.Size || !fileModeMatches(info, *item.Mode) {
			return reject("RESOURCE_FILE_CHANGED")
		}
		f, e := os.Open(name)
		if e != nil {
			return reject("RESOURCE_READ_FAILED")
		}
		defer f.Close()
		opened, e := f.Stat()
		if e != nil || !os.SameFile(info, opened) {
			return reject("RESOURCE_CHANGED")
		}
		hash := sha256.New()
		n, e := io.Copy(hash, io.LimitReader(f, *item.Size+1))
		if e != nil || n != *item.Size || hex.EncodeToString(hash.Sum(nil)) != item.SHA256 {
			return reject("RESOURCE_HASH_MISMATCH")
		}
		after, e := os.Lstat(name)
		if e != nil || !os.SameFile(info, after) || after.Mode() != info.Mode() || after.Size() != info.Size() {
			return reject("RESOURCE_CHANGED")
		}
		return nil
	})
	if e != nil {
		return empty, e
	}
	if len(seen) != len(m.Entries) {
		return empty, reject("RESOURCE_MISSING_MEMBER")
	}
	return m, nil
}
func execute(root string, m manifest, args []string) (int, error) {
	if len(args) == 0 || args[0] == "verify" && len(args) == 1 {
		fmt.Println(`{"status":"bootstrap_resources_verified","installable":false,"desktop_acceptance":false}`)
		return 0, nil
	}
	validSelfCheck := len(args) == 3 && args[0] == "self-check" && args[1] == "--output" && args[2] != ""
	validOfflineOperation := len(args) == 3 && args[0] == "offline-operation" && args[1] == "--request" && args[2] != ""
	validToolNameProfile := len(args) == 3 && args[0] == "tool-name-profile" && args[1] == "--request" && args[2] != ""
	validHostObservation := len(args) == 1 && args[0] == "observe-hosts"
	if !validSelfCheck && !validOfflineOperation && !validToolNameProfile && !validHostObservation {
		return 2, reject("BOOTSTRAP_ARGUMENTS_INVALID")
	}
	python := filepath.Join(root, filepath.FromSlash(m.Python))
	driver := filepath.Join(root, filepath.FromSlash(m.Driver))
	childArgs := append([]string{"-I", "-B", driver}, args...)
	cmd := exec.Command(python, childArgs...)
	cmd.Env = []string{"PATH=", "LANG=C", "LC_ALL=C"}
	if runtime.GOOS == "windows" {
		cmd.Env = append(cmd.Env, "SystemRoot="+os.Getenv("SystemRoot"))
	}
	cmd.Stdin = nil
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	e := cmd.Run()
	if e == nil {
		return 0, nil
	}
	var exitError *exec.ExitError
	if errors.As(e, &exitError) && exitError.ExitCode() >= 0 {
		return exitError.ExitCode(), nil
	}
	return 2, reject("BOOTSTRAP_CHILD_START_OR_SIGNAL_FAILED")
}
func run() (int, error) {
	executable, e := os.Executable()
	if e != nil {
		return 2, reject("BOOTSTRAP_LOCATION_UNAVAILABLE")
	}
	if e = noLinks(executable); e != nil {
		return 2, e
	}
	root := filepath.Dir(executable)
	m, e := verify(root, filepath.Base(executable), manifestSHA256)
	if e != nil {
		return 2, e
	}
	return execute(root, m, os.Args[1:])
}
func main() {
	code, e := run()
	if e != nil {
		fmt.Fprintln(os.Stderr, e.Error())
	}
	os.Exit(code)
}
