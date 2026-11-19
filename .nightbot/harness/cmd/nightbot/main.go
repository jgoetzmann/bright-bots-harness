// Command nightbot is the night bot's harness. See .nightbot/README.md.
package main

import (
	"os"

	"nightbot/internal/cli"
)

func main() {
	os.Exit(cli.Main(os.Args[1:], os.Stdout, os.Stderr))
}
