//go:build windows

package main

import "os"

// This engineering data verifier does not implement Windows private ACLs or
// claim native Windows execution. Production installation remains unavailable.
func singleOwnedFile(info os.FileInfo) bool              { return info.Mode().IsRegular() }
func ownedDirectory(info os.FileInfo) bool               { return info.IsDir() }
func fileModeMatches(info os.FileInfo, mode uint32) bool { return true }
