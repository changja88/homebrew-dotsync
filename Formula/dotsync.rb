class Dotsync < Formula
  desc "Sync app configs with a local folder"
  homepage "https://github.com/changja88/homebrew-dotsync"
  url "https://github.com/changja88/homebrew-dotsync/archive/refs/tags/v0.7.5.tar.gz"
  sha256 "8cd62899f559dba90511435c92582b52b18c6917308ee67573f1afceff38f6e6"
  license "MIT"

  # Reuse an existing Python 3.12+ binary if the user already has one — avoids
  # a duplicate ~100 MB python@3.12 install when they already use python.org,
  # pyenv, uv, or any other source. Canonical paths only (no PATH search and
  # no shell-out at formula load time).
  def self.external_python
    [
      "/opt/homebrew/bin/python3.12",
      "/opt/homebrew/bin/python3.13",
      "/usr/local/bin/python3.12",
      "/usr/local/bin/python3.13",
      "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12",
      "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13",
    ].find { |p| File.executable?(p) }
  end

  depends_on "python@3.12" if external_python.nil?
  # `dotsync sessions` reads compressed (.zst) Codex rollouts with `zstd -dc`.
  depends_on "zstd"

  def install
    libexec.install "lib/dotsync"
    libexec.install "bin"
    # Prefer an already-installed Python 3.12+ over brew's python@3.12; pin
    # the shebang so dotsync runs with a known version regardless of the
    # user's `python3` resolution.
    py = self.class.external_python || (formula_opt_bin("python@3.12")/"python3.12").to_s
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
