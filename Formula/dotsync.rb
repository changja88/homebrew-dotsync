class Dotsync < Formula
  desc "Sync app configs with a local folder"
  homepage "https://github.com/changja88/homebrew-dotsync"
  url "https://github.com/changja88/homebrew-dotsync/archive/refs/tags/v0.1.7.tar.gz"
  sha256 "c2e942a91b715259cf5831b1e4f212b8d39383971c65b0b5d68f5362dde05328"
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

  def install
    libexec.install "lib/dotsync"
    bin.install "bin/dotsync"
    # Prefer an already-installed Python 3.12+ over brew's python@3.12; pin
    # the shebang so dotsync runs with a known version regardless of the
    # user's `python3` resolution.
    py = self.class.external_python || (Formula["python@3.12"].opt_bin/"python3.12").to_s
    inreplace bin/"dotsync", /^#!.*python.*$/, "#!#{py}"
    bin.env_script_all_files(libexec/"bin", PYTHONPATH: libexec)
  end

  def caveats
    <<~EOS
      Get started:
        dotsync welcome   # quickstart guide
        dotsync init      # pick a sync folder + auto-detect apps

      `dotsync init` will print the exact `export DOTSYNC_DIR=...` line to
      add to your shell rc so dotsync works from any directory.
    EOS
  end

  test do
    assert_match "dotsync 0.1.7", shell_output("#{bin}/dotsync --version")
  end
end
