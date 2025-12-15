#!/usr/bin/env python3
"""
GhostStream Test Runner
Easy way to run all tests with various options.

Usage:
    python test.py           # Run all tests
    python test.py quick     # Run quick tests (skip slow)
    python test.py verbose   # Run with verbose output
    python test.py coverage  # Run with coverage report
    python test.py module    # Test specific module (e.g., python test.py transcoding)
"""

import sys
import subprocess
import os

def main():
    # Change to project directory
    project_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_dir)
    
    # Base pytest command
    cmd = [sys.executable, "-m", "pytest", "tests/"]
    
    # Parse arguments
    args = sys.argv[1:] if len(sys.argv) > 1 else []
    
    if not args:
        # Default: run all tests with summary
        cmd.extend(["-v", "--tb=short"])
        print("[TEST] Running all tests...\n")
        
    elif "quick" in args:
        # Quick: skip slow tests
        cmd.extend(["-v", "--tb=short", "-m", "not slow"])
        print("[QUICK] Running quick tests (skipping slow)...\n")
        
    elif "verbose" in args:
        # Verbose: show all output
        cmd.extend(["-v", "-s", "--tb=long"])
        print("[VERBOSE] Running tests with verbose output...\n")
        
    elif "coverage" in args:
        # Coverage: generate coverage report
        cmd.extend([
            "--cov=ghoststream",
            "--cov-report=term-missing",
            "--cov-report=html:coverage_html",
            "-v"
        ])
        print("[COVERAGE] Running tests with coverage report...\n")
        
    elif "failed" in args:
        # Re-run only failed tests
        cmd.extend(["--lf", "-v"])
        print("[RETRY] Re-running failed tests...\n")
        
    elif "watch" in args:
        # Watch mode (requires pytest-watch)
        cmd = [sys.executable, "-m", "pytest_watch", "--", "-v"]
        print("[WATCH] Starting watch mode (install pytest-watch if needed)...\n")
        
    else:
        # Assume it's a module name
        module = args[0]
        test_file = f"tests/test_{module}.py"
        if os.path.exists(test_file):
            cmd = [sys.executable, "-m", "pytest", test_file, "-v", "--tb=short"]
            print(f"[MODULE] Running tests for {module}...\n")
        else:
            # Try as a test pattern
            cmd.extend(["-v", "--tb=short", "-k", module])
            print(f"[FILTER] Running tests matching '{module}'...\n")
    
    # Run pytest
    try:
        result = subprocess.run(cmd)
        
        # Print summary
        print("\n" + "="*60)
        if result.returncode == 0:
            print("[PASS] All tests passed!")
        else:
            print(f"[FAIL] Tests failed (exit code: {result.returncode})")
        print("="*60)
        
        return result.returncode
        
    except KeyboardInterrupt:
        print("\n\n[ABORT] Tests interrupted by user")
        return 1
    except Exception as e:
        print(f"\n[ERROR] Error running tests: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
