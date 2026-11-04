class Timonel < Formula
  desc "Route coding tasks to the right CLI agent and model, escalate on failure"
  homepage "https://github.com/EduMayMar/Timonel"
  version "${version}"
  license "MIT"

  on_macos do
    on_arm do
      url "https://github.com/EduMayMar/Timonel/releases/download/v${version}/timonel-macos-arm64"
      sha256 "${sha_macos}"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/EduMayMar/Timonel/releases/download/v${version}/timonel-linux-x64"
      sha256 "${sha_linux}"
    end
  end

  depends_on "git"

  def install
    bin.install Dir["timonel-*"].first => "timonel"
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/timonel --version")
  end
end
