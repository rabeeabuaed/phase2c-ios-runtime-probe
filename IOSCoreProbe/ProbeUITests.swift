import XCTest

final class ProbeUITests: XCTestCase {
    func testRealApplicationRequests() throws {
        let environment = ProcessInfo.processInfo.environment
        guard let origin = environment["TCGEN_BACKEND_ORIGIN"],
              let session = environment["TCGEN_DASHBOARD_SESSION"], session.count == 32 else {
            XCTFail("Provide the controlled backend origin and an active Dashboard-created session ID")
            return
        }
        let app = XCUIApplication()
        app.launchEnvironment["TCGEN_BACKEND_ORIGIN"] = origin
        app.launchEnvironment["TCGEN_DASHBOARD_SESSION"] = session
        app.launch()
        for action in ["loginAlice", "profile", "items", "search", "create", "update", "role", "owned", "foreign", "error", "logout", "loginBob", "profile", "foreign"] {
            let button = app.buttons[action]
            for _ in 0..<8 where !button.isHittable { app.swipeUp() }
            if !button.isHittable { for _ in 0..<8 where !button.isHittable { app.swipeDown() } }
            XCTAssertTrue(button.waitForExistence(timeout: 10))
            XCTAssertTrue(button.isHittable)
            button.tap()
            let predicate = NSPredicate(format: "label BEGINSWITH %@", action + " HTTP ")
            expectation(for: predicate, evaluatedWith: app.staticTexts["requestStatus"])
            waitForExpectations(timeout: 20)
            let evidence = XCTAttachment(screenshot: app.screenshot())
            evidence.name = action
            evidence.lifetime = .keepAlways
            add(evidence)
        }
    }
}
