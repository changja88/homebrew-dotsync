import Foundation

/// dotsync's own rules, checked before calling it so the app can explain.
public enum AccountRules {
    public static let labelMax = 40

    /// dotsync's `validate_name`: ASCII letters and digits first, then
    /// letters, digits, ".", "_" or "-"; not "default".
    public static func isValidName(_ name: String) -> Bool {
        name != "default"
            && name.range(of: #"\A[A-Za-z0-9][A-Za-z0-9._-]*\z"#, options: .regularExpression) != nil
    }

    /// dotsync's `rename`: 1–40 characters after trimming, counted as
    /// Python counts them (Unicode code points).
    public static func isValidLabel(_ label: String) -> Bool {
        let trimmed = label.trimmingCharacters(in: .whitespacesAndNewlines)
        return (1...labelMax).contains(trimmed.unicodeScalars.count)
    }
}
