# iOS runtime integration fixture

Standalone SwiftUI app for an owned disposable backend. XCUITest taps actual native controls; URLSession performs all application requests.

The macOS workflow retains booted simulator output, build/install/launch command output, XCTest results, app-generated request receipts, exact backend HAR and artifact hashes. A small provenance ZIP has a GitHub-published SHA-256 digest for independent Dashboard verification. Artifacts expire after seven days; they contain only controlled test data and dummy credentials.

Only the newly authored fixture and workflow are published as source. No private application or security framework source is included. The backend origin must be an owned temporary Cloudflare Quick Tunnel.
