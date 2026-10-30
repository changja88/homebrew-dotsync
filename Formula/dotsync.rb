class Dotsync < Formula
  desc "Sync app configs with a local folder"
  homepage "https://github.com/changja88/homebrew-dotsync"
  url "https://github.com/changja88/homebrew-dotsync/archive/refs/tags/v0.8.2.tar.gz"
  sha256 "7faedcec53e5a1e8cecead9c0671e1d220fcc36bfd80535f21db864e1dede8c6"
  license "MIT"

  # dotsync needs Python 3.14+: `dotsync sessions` reads compressed (.zst)
  # Codex rollouts with its `compression.zstd`. Reuse an existing 3.14 binary
  # if the user already has one — avoids a duplicate ~100 MB python@3.14
  # install when they already use python.org, pyenv, uv, or any other source.
  # Canonical paths only (no PATH search and no shell-out at formula load time).
  def self.external_python
    [
      "/opt/homebrew/bin/python3.14",
      "/usr/local/bin/python3.14",
      "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14",
    ].find { |p| File.executable?(p) }
  end

  depends_on "python@3.14" if external_python.nil?

  def install
    libexec.install "lib/dotsync"
    libexec.install "bin"
    # Prefer an already-installed Python 3.14 over brew's python@3.14; pin
    # the shebang so dotsync runs with a known version regardless of the
    # user's `python3` resolution.
    py = self.class.external_python || (formula_opt_bin("python@3.14")/"python3.14").to_s
    inreplace libexec/"bin/dotsync", /^#!.*python.*$/, "#!#{py}"
    (bin/"dotsync").write_env_script libexec/"bin/dotsync", PYTHONPATH: libexec
  end

  def caveats
    <<~EOS
      dotsync is run by dotsync.app:
        brew install --cask changja88/dotsync/dotsync-app
      Choose the sync folder and the apps in its 설정.
    EOS
  end

  test do
    assert_match "dotsync #{version}", shell_output("#{bin}/dotsync --version")
  end
end
