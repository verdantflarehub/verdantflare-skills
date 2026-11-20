package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func i64(v int64) *int64   { return &v }
func u32(v uint32) *uint32 { return &v }
func fixture(t *testing.T) (string, manifest, []byte) {
	t.Helper()
	// Go's default TempDir is not used: all one-off files belong to repo .tmp.
	base := filepath.Join("..", "..", "..", "..", "..", ".tmp", "bootstrap-native-tests")
	if e := os.MkdirAll(base, 0700); e != nil {
		t.Fatal(e)
	}
	root, e := os.MkdirTemp(base, "case-")
	if e != nil {
		t.Fatal(e)
	}
	root, e = filepath.Abs(root)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { os.RemoveAll(root) })
	m := manifest{Schema: "vf.installer-bootstrap/v1", Platform: hostPlatform(), Python: "resources/runtime/python/bin/python3.12", Driver: "resources/installer/driver.py", Entries: map[string]entry{}}
	files := map[string][]byte{m.Python: []byte("#!/bin/sh\nexit 0\n"), m.Driver: []byte("# non-executed fixture\n"), "resources/vendor/example.py": []byte("# pinned fixture\n")}
	for name, raw := range files {
		full := filepath.Join(root, filepath.FromSlash(name))
		if e = os.MkdirAll(filepath.Dir(full), 0700); e != nil {
			t.Fatal(e)
		}
		mode := uint32(0644)
		if name == m.Python {
			mode = 0755
		}
		if e = os.WriteFile(full, raw, os.FileMode(mode)); e != nil {
			t.Fatal(e)
		}
		m.Entries[name] = entry{Type: "file", Size: i64(int64(len(raw))), SHA256: digest(raw), Mode: u32(mode)}
	}
	raw, e := json.Marshal(m)
	if e != nil {
		t.Fatal(e)
	}
	if e = os.WriteFile(filepath.Join(root, "bootstrap-manifest.json"), raw, 0644); e != nil {
		t.Fatal(e)
	}
	if e = os.WriteFile(filepath.Join(root, "vf-bootstrap"), []byte("native fixture"), 0755); e != nil {
		t.Fatal(e)
	}
	return root, m, raw
}
func writeManifest(t *testing.T, root string, m manifest) []byte {
	t.Helper()
	raw, e := json.Marshal(m)
	if e != nil {
		t.Fatal(e)
	}
	if e = os.WriteFile(filepath.Join(root, "bootstrap-manifest.json"), raw, 0644); e != nil {
		t.Fatal(e)
	}
	return raw
}
func TestValidExactResourcesAndNoopVerification(t *testing.T) {
	root, _, raw := fixture(t)
	if _, e := verify(root, "vf-bootstrap", digest(raw)); e != nil {
		t.Fatal(e)
	}
	if _, e := verify(root, "vf-bootstrap", strings.Repeat("0", 64)); e == nil {
		t.Fatal("wrong external pin accepted")
	}
}
func TestResourceTamperMissingExtraDirectoryAndMode(t *testing.T) {
	for _, kind := range []string{"hash", "missing", "extra", "extra-dir", "mode", "hardlink", "parent-link"} {
		t.Run(kind, func(t *testing.T) {
			root, m, raw := fixture(t)
			driver := filepath.Join(root, filepath.FromSlash(m.Driver))
			switch kind {
			case "hash":
				os.WriteFile(driver, []byte(strings.Repeat("x", int(*m.Entries[m.Driver].Size))), 0644)
			case "missing":
				os.Remove(driver)
			case "extra":
				os.WriteFile(filepath.Join(root, "unknown"), nil, 0644)
			case "extra-dir":
				os.Mkdir(filepath.Join(root, "unknown"), 0700)
			case "mode":
				os.Chmod(driver, 0600)
			case "hardlink":
				os.Link(driver, filepath.Join(filepath.Dir(root), filepath.Base(root)+"-hardlink"))
				t.Cleanup(func() { os.Remove(filepath.Join(filepath.Dir(root), filepath.Base(root)+"-hardlink")) })
			case "parent-link":
				os.Remove(driver)
				os.Remove(filepath.Dir(driver))
				os.Symlink(filepath.Join(root, "resources/vendor"), filepath.Dir(driver))
			}
			if _, e := verify(root, "vf-bootstrap", digest(raw)); e == nil {
				t.Fatal("unsafe resource accepted")
			}
		})
	}
}
func TestLinkTargetsCyclesEscapeAndSymlinkReplacedByFile(t *testing.T) {
	root, m, _ := fixture(t)
	name := "resources/runtime/python/bin/python3"
	m.Entries[name] = entry{Type: "symlink", Target: "python3.12"}
	link := filepath.Join(root, filepath.FromSlash(name))
	if e := os.Symlink("python3.12", link); e != nil {
		t.Fatal(e)
	}
	raw := writeManifest(t, root, m)
	if _, e := verify(root, "vf-bootstrap", digest(raw)); e != nil {
		t.Fatal(e)
	}
	os.Remove(link)
	os.WriteFile(link, []byte("python3.12"), 0644)
	if _, e := verify(root, "vf-bootstrap", digest(raw)); e == nil {
		t.Fatal("regular file accepted as link")
	}
	for _, target := range []string{"../../../../../../outside", "python3", "missing", "python3.12/../python3.12"} {
		m.Entries[name] = entry{Type: "symlink", Target: target}
		bad := writeManifest(t, root, m)
		if _, e := decodeManifest(bad, digest(bad)); e == nil {
			t.Fatalf("bad target accepted: %s", target)
		}
	}
}
func TestDuplicateKeysUnknownFieldsNullAndEntrypoints(t *testing.T) {
	_, m, raw := fixture(t)
	cases := [][]byte{
		[]byte(strings.Replace(string(raw), `"schema":`, `"schema":"duplicate","schema":`, 1)),
		[]byte(strings.Replace(string(raw), `"type":"file"`, `"type":"file","target":null`, 1)),
		[]byte(strings.Replace(string(raw), `"type":"file"`, `"type":"file","unexpected":true`, 1)),
		[]byte(string(raw) + " {}"),
	}
	for _, bad := range cases {
		if _, e := decodeManifest(bad, digest(bad)); e == nil {
			t.Fatal("invalid JSON shape accepted")
		}
	}
	m.Python = "resources/vendor/example.py"
	bad, _ := json.Marshal(m)
	if _, e := decodeManifest(bad, digest(bad)); e == nil {
		t.Fatal("arbitrary entrypoint accepted")
	}
}
func TestAliasPrefixReservedAndUnboundedShape(t *testing.T) {
	for _, name := range []string{"../escape", "resources/CON.txt", "resources/vendor/../other", "resources/Vendor/other.py", "resources/vendor/example.py/child"} {
		t.Run(name, func(t *testing.T) {
			_, m, _ := fixture(t)
			m.Entries[name] = entry{Type: "file", Size: i64(0), Mode: u32(0644), SHA256: digest(nil)}
			raw, _ := json.Marshal(m)
			if _, e := decodeManifest(raw, digest(raw)); e == nil {
				t.Fatal("unsafe path accepted")
			}
		})
	}
	_, m, _ := fixture(t)
	item := m.Entries[m.Driver]
	item.Size = i64(maxFile + 1)
	m.Entries[m.Driver] = item
	raw, _ := json.Marshal(m)
	if _, e := decodeManifest(raw, digest(raw)); e == nil {
		t.Fatal("oversize accepted")
	}
	deep := []byte(strings.Repeat("[", 66) + "0" + strings.Repeat("]", 66))
	if e := uniqueJSON(deep); e == nil {
		t.Fatal("deep structure accepted")
	}
}
func TestNoInvocationForUnknownArgumentsAndChildExitPropagates(t *testing.T) {
	root, m, _ := fixture(t)
	if code, e := execute(root, m, []string{"arbitrary-program"}); e == nil || code != 2 {
		t.Fatal("unknown command accepted")
	}
	// Synthetic interpreter is deliberately a shell fixture, not Python evidence.
	python := filepath.Join(root, filepath.FromSlash(m.Python))
	os.WriteFile(python, []byte("#!/bin/sh\nexit 23\n"), 0755)
	if code, e := execute(root, m, []string{"self-check", "--output", "explicit-fixture"}); e != nil || code != 23 {
		t.Fatal(code, e)
	}
	if code, e := execute(root, m, []string{"offline-operation", "--request", "explicit-fixture"}); e != nil || code != 23 {
		t.Fatal(code, e)
	}
	if code, e := execute(root, m, []string{"tool-name-profile", "--request", "explicit-fixture"}); e != nil || code != 23 {
		t.Fatal(code, e)
	}
	if code, e := execute(root, m, []string{"observe-hosts"}); e != nil || code != 23 {
		t.Fatal(code, e)
	}
	for _, args := range [][]string{{"offline-operation", "--output", "x"}, {"self-check", "--request", "x"}, {"offline-operation", "--request", "x", "extra"}, {"tool-name-profile", "--output", "x"}, {"tool-name-profile", "--request", "x", "extra"}, {"observe-hosts", "--request", "x"}, {"observe-hosts", "extra"}} {
		if code, e := execute(root, m, args); e == nil || code != 2 {
			t.Fatal("invalid command shape accepted", args)
		}
	}
	os.Remove(python)
	if code, e := execute(root, m, []string{"self-check", "--output", "explicit-fixture"}); e == nil || code != 2 {
		t.Fatal("missing interpreter accepted")
	}
}
