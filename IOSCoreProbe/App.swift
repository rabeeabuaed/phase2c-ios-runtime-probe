import SwiftUI

@main
struct IOSCoreProbeApp: App {
    var body: some Scene { WindowGroup { ProbeView() } }
}

struct ProbeView: View {
    @State private var origin = ProcessInfo.processInfo.environment["TCGEN_BACKEND_ORIGIN"] ?? "http://127.0.0.1:8879"
    @State private var session = ProcessInfo.processInfo.environment["TCGEN_DASHBOARD_SESSION"] ?? ""
    @State private var token = ""
    @State private var status = "Ready"
    @State private var busy = false

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 14) {
                    TextField("Controlled backend origin", text: $origin)
                        .textInputAutocapitalization(.never).accessibilityIdentifier("backendOrigin")
                    TextField("Dashboard session ID", text: $session)
                        .textInputAutocapitalization(.never).accessibilityIdentifier("dashboardSession")
                    Text(status).accessibilityIdentifier("requestStatus")
                    action("Login Alice", "loginAlice", "POST", "/login", ["identity": "alice"])
                    action("Load profile", "profile", "GET", "/me")
                    action("Load items", "items", "GET", "/items?limit=2&page=1")
                    action("Search items", "search", "GET", "/items?limit=2&search=desk&tag=one&tag=two")
                    action("Create item", "create", "POST", "/items", ["name": "desk", "quantity": 2])
                    action("Update owned item", "update", "PUT", "/items/101", ["name": "desk", "quantity": 3])
                    action("Role-protected operation", "role", "GET", "/admin/report")
                    action("Open owned object", "owned", "GET", "/items/101")
                    action("Open foreign-object context", "foreign", "GET", "/items/202")
                    action("Controlled error response", "error", "GET", "/diagnostics")
                    action("Logout", "logout", "POST", "/logout", [:])
                    action("Login Bob", "loginBob", "POST", "/login", ["identity": "bob"])
                }.padding()
            }.navigationTitle("iOS API Core Probe")
        }
    }

    private func action(_ title: String, _ id: String, _ method: String, _ path: String,
                        _ body: [String: Any]? = nil) -> some View {
        Button(title) { Task { await send(id, method, path, body) } }
            .accessibilityIdentifier(id).disabled(busy)
    }

    @MainActor
    private func send(_ action: String, _ method: String, _ path: String, _ body: [String: Any]?) async {
        guard session.count == 32, let url = URL(string: origin + path) else {
            status = "Enter a backend origin and Dashboard session ID"; return
        }
        busy = true
        defer { busy = false }
        do {
            var request = URLRequest(url: url)
            request.httpMethod = method
            request.setValue(session, forHTTPHeaderField: "X-Dashboard-Session")
            request.setValue("application/json", forHTTPHeaderField: "Accept")
            if !token.isEmpty { request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization") }
            if let body = body {
                request.httpBody = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            }
            let (data, response) = try await URLSession.shared.data(for: request)
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            if path == "/login", let value = try JSONSerialization.jsonObject(with: data) as? [String: Any],
               let credential = value["token"] as? String { token = credential }
            if path == "/logout" { token = "" }
            // Display factual networking status only. Security results belong to the Dashboard.
            status = action + " HTTP " + String(code)
        } catch { status = action + " request failed: " + error.localizedDescription }
    }
}
