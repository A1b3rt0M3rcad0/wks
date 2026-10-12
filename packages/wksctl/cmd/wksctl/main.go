package main

import (
	"github.com/A1b3rt0M3rcad0/wks/packages/wksctl/internal/cli"
	"os"
)

var version = "development"
var commit = "development"

func main() { os.Exit(cli.Run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr, version, commit)) }
