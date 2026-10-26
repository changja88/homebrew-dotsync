import DotsyncKit
import Foundation

/// Where the executable starts. launchd runs it with `--auto-switch-check`
/// whenever the app group folder changes: check, and exit without starting
/// the app.
@main
enum Entry {
    static func main() {
        if CommandLine.arguments.dropFirst().first == AutoSwitchAgent.checkArgument {
            exit(CheckMode.run())
        }
        DotsyncApp.main()
    }
}
