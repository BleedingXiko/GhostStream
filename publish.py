#!/usr/bin/env python3
"""
Automated SDK Publishing Script for GhostStream
Handles version bumping, building, and publishing for both Python and npm packages
"""
import os
import sys
import shutil
import subprocess
import re
from pathlib import Path
import shlex

ROOT_DIR = Path(__file__).parent
SDK_JS_DIR = ROOT_DIR / "sdk" / "js"
PYTHON_INIT = ROOT_DIR / "ghoststream" / "__init__.py"
JS_PACKAGE_JSON = SDK_JS_DIR / "package.json"
DIST_DIR = ROOT_DIR / "dist"
PYTHON_CMD = shlex.quote(sys.executable)


def run_cmd(cmd, cwd=None, check=True):
    """Run command and return output"""
    print(f"\n→ Running: {cmd}")
    result = subprocess.run(
        cmd, 
        shell=True, 
        cwd=cwd or ROOT_DIR,
        capture_output=True,
        text=True
    )
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if check and result.returncode != 0:
        print(f"✗ Command failed with exit code {result.returncode}")
        sys.exit(1)
    return result


def get_current_version(pkg_type):
    """Get current version from package files"""
    if pkg_type == "python":
        content = PYTHON_INIT.read_text(encoding="utf-8")
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
        return match.group(1) if match else None
    elif pkg_type == "npm":
        content = JS_PACKAGE_JSON.read_text(encoding="utf-8")
        match = re.search(r'"version"\s*:\s*"([^"]+)"', content)
        return match.group(1) if match else None


def bump_version(version, bump_type="patch"):
    """Bump semantic version"""
    major, minor, patch = map(int, version.split("."))
    if bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    else:  # patch
        return f"{major}.{minor}.{patch + 1}"


def update_version(pkg_type, new_version):
    """Update version in package files"""
    if pkg_type == "python":
        content = PYTHON_INIT.read_text(encoding="utf-8")
        content = re.sub(
            r'(__version__\s*=\s*["\'])[^"\']+(["\'])',
            rf'\g<1>{new_version}\g<2>',
            content
        )
        PYTHON_INIT.write_text(content, encoding="utf-8")
        print(f"✓ Updated Python version to {new_version}")
    elif pkg_type == "npm":
        content = JS_PACKAGE_JSON.read_text(encoding="utf-8")
        content = re.sub(
            r'("version"\s*:\s*")[^"]+(")',
            rf'\g<1>{new_version}\g<2>',
            content
        )
        JS_PACKAGE_JSON.write_text(content, encoding="utf-8")
        print(f"✓ Updated npm version to {new_version}")


def clean_dist():
    """Remove old distribution files"""
    if DIST_DIR.exists():
        print(f"→ Cleaning {DIST_DIR}")
        shutil.rmtree(DIST_DIR)
        print("✓ Cleaned dist directory")


def publish_python(skip_build=False):
    """Build and publish Python package to PyPI"""
    print("\n" + "="*60)
    print("📦 Publishing Python Package to PyPI")
    print("="*60)
    
    # Clean old builds
    clean_dist()
    
    if not skip_build:
        # Build package
        print("\n→ Building Python package...")
        run_cmd(f"{PYTHON_CMD} -m build --no-isolation")
        print("✓ Python package built successfully")
    
    # Upload to PyPI
    print("\n→ Uploading to PyPI...")
    run_cmd(f"{PYTHON_CMD} -m twine upload --skip-existing dist/*")
    print("✓ Python package published to PyPI!")


def publish_npm(npm_otp=None):
    """Build and publish npm package"""
    print("\n" + "="*60)
    print("📦 Publishing npm Package")
    print("="*60)
    
    # npm will run prepublishOnly automatically which builds
    print("\n→ Publishing to npm (will auto-build)...")
    publish_cmd = "npm publish"
    if npm_otp:
        publish_cmd = f"npm publish --otp={shlex.quote(str(npm_otp))}"

    result = run_cmd(publish_cmd, cwd=SDK_JS_DIR, check=False)
    if result.returncode == 0:
        print("✓ npm package published!")
        return

    stderr_text = result.stderr or ""
    if "EOTP" in stderr_text and not npm_otp:
        print("\n→ npm requires an interactive publish challenge.")
        print("→ If npm printed a browser/passkey link, complete it now in your browser.")
        print("→ If your account uses an authenticator code instead, enter it when prompted below.")

        otp = input("Press Enter to retry after browser auth, or type a 6-digit OTP: ").strip()
        retry_cmd = "npm publish"
        if otp:
            retry_cmd = f"npm publish --otp={shlex.quote(otp)}"

        retry_result = run_cmd(retry_cmd, cwd=SDK_JS_DIR, check=False)
        if retry_result.returncode == 0:
            print("✓ npm package published!")
            return

        print(f"✗ npm publish failed with exit code {retry_result.returncode}")
        sys.exit(retry_result.returncode)

    print(f"✗ npm publish failed with exit code {result.returncode}")
    sys.exit(result.returncode)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Publish GhostStream SDKs")
    parser.add_argument(
        "package",
        choices=["python", "npm", "both"],
        help="Which package to publish"
    )
    parser.add_argument(
        "--bump",
        choices=["major", "minor", "patch"],
        default="patch",
        help="Version bump type (default: patch)"
    )
    parser.add_argument(
        "--no-version-bump",
        action="store_true",
        help="Skip version bumping (use current version)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build but don't publish"
    )
    parser.add_argument(
        "--npm-otp",
        type=str,
        default=None,
        help="One-time password for npm publish when account 2FA is enabled"
    )
    
    args = parser.parse_args()
    
    packages = ["python", "npm"] if args.package == "both" else [args.package]
    
    for pkg in packages:
        current_version = get_current_version(pkg)
        print(f"\nCurrent {pkg} version: {current_version}")
        
        if not args.no_version_bump:
            new_version = bump_version(current_version, args.bump)
            print(f"New {pkg} version: {new_version}")
            
            confirm = input(f"Update {pkg} to v{new_version}? (y/n): ").lower()
            if confirm != 'y':
                print("Aborted.")
                continue
            
            update_version(pkg, new_version)
        
        if args.dry_run:
            print(f"→ DRY RUN: Would publish {pkg}")
            continue
        
        try:
            if pkg == "python":
                publish_python()
            else:
                publish_npm(args.npm_otp)
        except Exception as e:
            print(f"✗ Error publishing {pkg}: {e}")
            sys.exit(1)
    
    print("\n" + "="*60)
    print("✓ All packages published successfully!")
    print("="*60)


if __name__ == "__main__":
    main()
