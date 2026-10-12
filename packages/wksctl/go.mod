module github.com/A1b3rt0M3rcad0/wks/packages/wksctl

go 1.27.0

replace github.com/A1b3rt0M3rcad0/wks/packages/wks-sdk-go => ../wks-sdk-go

require (
	github.com/A1b3rt0M3rcad0/wks/packages/wks-sdk-go v0.0.0
	golang.org/x/sys v0.37.0
	gopkg.in/yaml.v3 v3.0.1
)
