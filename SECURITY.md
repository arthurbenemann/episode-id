# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it
privately rather than opening a public issue.

Use [GitHub's private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
via the "Security" tab of this repository.

You should receive an acknowledgement within a few days.

## Trust model

This is a single-user tool designed for a trusted, local home-LAN deployment.
It ships with no authentication and assumes the host running it is
trusted.

If you expose the (eventual) web UI beyond your private network, put it
behind your own authentication layer — a reverse proxy with basic-auth or
SSO is the recommended approach.

The tool reads MKV files from a configured input folder and writes renamed
files into a configured library folder. It does not exfiltrate file contents
anywhere; only subtitle dialogue is sent to configured providers (e.g.
chakoteya.net, OpenSubtitles) for matching.
