# DotSync macOS 공개 배포 설정

이 문서는 `DotSync.app`을 Developer ID로 서명하고 Apple 공증을 거쳐
GitHub Release와 Homebrew Cask로 공개할 때 필요한 로컬 설정을 설명합니다.
Mac App Store 배포 절차가 아닙니다.

## 1. 로컬 설정 파일 준비

저장소 루트에서 다음 명령을 실행합니다.

```bash
cp config/macos-release.env.example config/macos-release.env
chmod 600 config/macos-release.env
```

`config/macos-release.env`는 Git에서 제외됩니다. 다음 값만 기록하고 Apple
계정 비밀번호, 앱 전용 암호, 인증서 개인키, App Store Connect API 키는
절대 기록하지 마세요.

```bash
export DEVELOPER_ID_APPLICATION=''
export NOTARYTOOL_PROFILE='dotsync-notary'
export APPLE_TEAM_ID=''
export APPLE_ACCOUNT_EMAIL=''
```

## 2. Developer ID Application 확인

이 Mac의 Keychain에 설치된 코드 서명 인증서를 확인합니다.

```bash
security find-identity -v -p codesigning
```

출력에서 다음 형식의 따옴표 안 문자열을 정확히 복사해
`DEVELOPER_ID_APPLICATION`에 넣습니다.

```text
Developer ID Application: 개발자 또는 회사 이름 (TEAMID1234)
```

예를 들어 출력이 다음과 같다면:

```text
1) ABCDEF0123456789ABCDEF0123456789ABCDEF01 "Developer ID Application: Example, Inc. (TEAMID1234)"
```

설정은 다음과 같습니다.

```bash
export DEVELOPER_ID_APPLICATION='Developer ID Application: Example, Inc. (TEAMID1234)'
```

해당 항목이 없다면 [Apple Developer 계정](https://developer.apple.com/account/)
에서 `Certificates, Identifiers & Profiles` → `Certificates` → `+` →
`Developer ID` → `Developer ID Application`을 선택해 인증서를 생성합니다.
인증서를 내려받아 더블 클릭하면 Keychain에 설치됩니다. 인증서를 생성한
Mac의 Keychain에는 인증서와 연결된 개인키도 함께 있어야 합니다.

Apple 공식 안내:
[Developer ID 인증서 생성](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/)

## 3. Team ID 확인

[Apple Developer 계정](https://developer.apple.com/account/)에 로그인한 뒤
`Membership details`에서 10자리 Team ID를 확인해 `APPLE_TEAM_ID`에
입력합니다.

```bash
export APPLE_TEAM_ID='TEAMID1234'
```

Apple 공식 안내:
[Team ID 찾기](https://developer.apple.com/help/glossary/team-id/)

## 4. Apple 계정 이메일 입력

Apple Developer Program에 가입한 Apple Account 이메일을
`APPLE_ACCOUNT_EMAIL`에 입력합니다.

```bash
export APPLE_ACCOUNT_EMAIL='developer@example.com'
```

이 이메일은 공증 자격을 Keychain에 처음 저장할 때만 사용합니다. Apple
계정 비밀번호는 설정 파일에 넣지 않습니다.

## 5. 공증용 앱 전용 암호 생성

[Apple Account](https://account.apple.com/)에 로그인하고
`Sign-In and Security` → `App-Specific Passwords`에서 공증용 앱 전용
암호를 생성합니다. Apple 계정 자체 비밀번호를 사용하지 마세요.

Apple 공식 안내:
[앱 전용 암호 생성](https://support.apple.com/102654)

## 6. notarytool 프로필을 Keychain에 저장

설정 파일을 현재 셸에 불러옵니다.

```bash
source config/macos-release.env
```

프로필 이름은 로컬에서 정하는 별칭입니다. 기본값
`dotsync-notary`를 그대로 사용해도 됩니다. 다음 명령을 실행하면
`notarytool`이 앱 전용 암호를 대화형으로 요청하고, 입력한 자격정보를
macOS Keychain에 저장합니다.

```bash
xcrun notarytool store-credentials "$NOTARYTOOL_PROFILE" \
  --apple-id "$APPLE_ACCOUNT_EMAIL" \
  --team-id "$APPLE_TEAM_ID"
```

암호를 명령행 옵션이나 파일에 적지 마세요. 프롬프트가 나타났을 때 앱
전용 암호를 입력합니다.

저장된 프로필을 검증합니다.

```bash
xcrun notarytool history \
  --keychain-profile "$NOTARYTOOL_PROFILE" \
  --output-format json
```

정상적인 JSON이 출력되면 `NOTARYTOOL_PROFILE` 설정이 완료된 것입니다.

Apple 공식 안내:
[notarytool 자격정보를 Keychain에 저장하기](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow)

## 7. GitHub 권한 확인

릴리스 스크립트는 `changja88/homebrew-dotsync`의 기존 GitHub Release에
서명·공증된 ZIP을 업로드합니다. 현재 로그인과 저장소 접근 권한을
확인합니다.

```bash
gh auth status
gh repo view changja88/homebrew-dotsync
```

GitHub 토큰을 이 설정 파일에 기록하지 마세요. `gh auth login`이 관리하는
보안 저장소를 사용합니다.

## 8. 입력값 확인

로컬 설정 파일을 불러온 뒤 필수 값이 비어 있지 않은지 확인합니다.

```bash
source config/macos-release.env
printf 'Developer ID: %s\n' "$DEVELOPER_ID_APPLICATION"
printf 'Notary profile: %s\n' "$NOTARYTOOL_PROFILE"
printf 'Team ID: %s\n' "$APPLE_TEAM_ID"
printf 'Apple account: %s\n' "$APPLE_ACCOUNT_EMAIL"
```

`DEVELOPER_ID_APPLICATION`은 `security find-identity` 출력과 글자 단위로
일치해야 합니다. `NOTARYTOOL_PROFILE`은 `notarytool history`로 검증된
Keychain 프로필 이름이어야 합니다.

## 9. 공개 릴리스 실행

실제 공개 릴리스 직전에만 다음 명령을 실행합니다.

```bash
source config/macos-release.env
bash scripts/release_macos_app.sh X.Y.Z
```

`X.Y.Z`는 현재 프로젝트 버전 및 현재 HEAD의 annotated tag와 정확히
일치해야 합니다. 이 스크립트는 실제 Apple 공증 요청과 GitHub Release
자산 업로드를 수행하므로 시험 삼아 실행하지 마세요. 성공 후 생성된
`Casks/dotsync-app.rb`을 검토·커밋·푸시해야 Homebrew Cask 공개가
완료됩니다.

## 보안 체크리스트

- `config/macos-release.env`만 로컬 값을 보관하며 Git에 커밋하지 않습니다.
- Apple 계정 비밀번호와 앱 전용 암호는 파일에 저장하지 않습니다.
- 인증서 개인키나 `.p8` 파일을 저장소에 복사하지 않습니다.
- 공증 자격은 `notarytool store-credentials`를 통해 Keychain에 보관합니다.
- GitHub 인증은 `gh`의 보안 저장소를 사용합니다.
- 화면 공유나 이슈에 `security find-identity` 전체 출력을 그대로 올리지
  않습니다.
