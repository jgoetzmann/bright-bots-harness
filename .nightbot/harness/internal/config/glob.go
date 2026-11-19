package config

import (
	"regexp"
	"strings"
)

// MatchAny says whether path (slash-separated, relative to the repository root) matches any of
// the patterns. A pattern is a glob: `*` and `?` stay within one path segment, `**` crosses
// segments, and a pattern ending in `/` matches everything under that directory.
func MatchAny(patterns []string, path string) bool {
	for _, pattern := range patterns {
		if re, err := globRegexp(pattern); err == nil && re.MatchString(path) {
			return true
		}
	}
	return false
}

func globRegexp(pattern string) (*regexp.Regexp, error) {
	pattern = strings.TrimPrefix(pattern, "/")
	if strings.HasSuffix(pattern, "/") {
		pattern += "**"
	}
	var b strings.Builder
	b.WriteString("^")
	for i := 0; i < len(pattern); i++ {
		switch ch := pattern[i]; ch {
		case '*':
			if i+1 < len(pattern) && pattern[i+1] == '*' {
				i++
				if i+1 < len(pattern) && pattern[i+1] == '/' {
					i++
					b.WriteString("(?:.*/)?") // **/ : any number of directories, none included
				} else {
					b.WriteString(".*")
				}
			} else {
				b.WriteString("[^/]*")
			}
		case '?':
			b.WriteString("[^/]")
		default:
			b.WriteString(regexp.QuoteMeta(string(ch)))
		}
	}
	b.WriteString("$")
	return regexp.Compile(b.String())
}
