class Dotsync < Formula
  desc "Sync app configs with a local folder"
  homepage "https://github.com/changja88/homebrew-dotsync"
  url "https://github.com/changja88/homebrew-dotsync/archive/refs/tags/v0.1.4.tar.gz"
  sha256 "b923b3afd318e751b2e38d00b24ca2025891e9ee2fb85ff81b63a9c181114cf5"
  license "MIT"
  depends_on "python@3.12"

  def install
    libexec.install "lib/dotsync"
    # Install the entry script and pin its shebang to python@3.12 so users
    # don't accidentally run dotsync under an older system python3.
    bin.install "bin/dotsync"
    py = Formula["python@3.12"].opt_bin/"python3.12"
    inreplace bin/"dotsync", %r{^#!.*python.*$}, "#!#{py}"
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
    assert_match "dotsync 0.1.4", shell_output("#{bin}/dotsync --version")
  end
end
