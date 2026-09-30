# iOS runtime integration fixture

Small, standalone SwiftUI test app for an owned disposable API backend. The workflow builds and runs Apple iOS Simulator on a standard GitHub-hosted macOS runner. XCUITest taps native app controls; URLSession performs the requests.

This repository contains only the test fixture. It contains no private application source, credentials, security framework source, or captured HTTP data. Runtime artifacts expire after one day. The optional backend origin must be a temporary owned Cloudflare Quick Tunnel; no third-party target is supported.
