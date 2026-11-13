#!/usr/bin/env osascript -l JavaScript
// ==============================================================================
// EARS — Native macOS Cocoa GUI Setup & Uninstall Wizard (JXA / AppKit)
// Runs on 100% bone-stock macOS via `/usr/bin/osascript -l JavaScript` with
// zero external dependencies. Provides a live progress bar, live step status,
// and a real-time scrolling Technical Log during installation and uninstallation.
// ==============================================================================

ObjC.import("Cocoa");
ObjC.import("Foundation");
ObjC.import("stdlib");

function run(argv) {
    var mode = (argv && argv.length > 0) ? String(argv[0]) : "install";
    var appDir = (argv && argv.length > 1) ? String(argv[1]) : ".";
    var isInstall = (mode === "install");

    var app = $.NSApplication.sharedApplication;
    app.setActivationPolicy($.NSApplicationActivationPolicyRegular);

    var winWidth = 560;
    var winHeight = 420;
    var rect = $.NSMakeRect(0, 0, winWidth, winHeight);
    var styleMask = $.NSTitledWindowMask | $.NSClosableWindowMask | $.NSMiniaturizableWindowMask;

    var win = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer(
        rect,
        styleMask,
        $.NSBackingStoreBuffered,
        false
    );
    win.center;
    win.title = isInstall
        ? "Ears Audio Captioner — macOS Setup Wizard"
        : "Ears Audio Captioner — macOS Uninstaller";
    win.backgroundColor = $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.051, 0.039, 0.090, 1.0);

    var content = win.contentView;

    function makeLabel(text, x, y, w, h, size, bold, r, g, b) {
        var lbl = $.NSTextField.alloc.initWithFrame($.NSMakeRect(x, y, w, h));
        lbl.stringValue = text;
        lbl.bezeled = false;
        lbl.drawsBackground = false;
        lbl.editable = false;
        lbl.selectable = false;
        lbl.font = bold ? $.NSFont.boldSystemFontOfSize(size) : $.NSFont.systemFontOfSize(size);
        lbl.textColor = $.NSColor.colorWithCalibratedRedGreenBlueAlpha(r, g, b, 1.0);
        content.addSubview(lbl);
        return lbl;
    }

    // Header
    makeLabel(
        isInstall ? "Install Ears Audio Captioner" : "Uninstall Ears Audio Captioner",
        24, 372, 512, 28, 18, true, 0.945, 0.961, 0.976
    );
    makeLabel(
        isInstall
            ? "100% local CCTV & IP camera speech-to-text captioning in your macOS Menu Bar."
            : "Cleanly remove Ears while leaving your pre-existing system packages untouched.",
        24, 348, 512, 20, 12, false, 0.616, 0.580, 0.722
    );

    // Info / Options Box
    var infoText = isInstall
        ? "• Installs /Applications/Ears.app & /Applications/Uninstall Ears.app\n" +
          "• Sets up isolated Python virtual environment, FFmpeg & Whisper engine\n" +
          "• Note: The Web Setup Wizard (http://127.0.0.1:8765) will open automatically."
        : "• Stops the Ears Menu Bar process & removes /Applications/Ears.app\n" +
          "• Removes ./venv while leaving Homebrew & system Python untouched";
    makeLabel(infoText, 28, 282, 504, 58, 12, false, 0.82, 0.80, 0.92);

    // Uninstall Checkbox for deleting transcripts
    var chkPurge = null;
    if (!isInstall) {
        chkPurge = $.NSButton.alloc.initWithFrame($.NSMakeRect(28, 254, 480, 22));
        chkPurge.setButtonType($.NSSwitchButton);
        chkPurge.title = "Also delete my saved transcripts, audio clips, and config.json";
        chkPurge.state = $.NSOffState;
        content.addSubview(chkPurge);
    }

    // Live Status Label
    var statusLbl = makeLabel(
        "Folder: " + appDir,
        24, 224, 512, 20, 12, true, 0.616, 0.482, 0.918
    );

    // Determinate Progress Bar
    var pbar = $.NSProgressIndicator.alloc.initWithFrame($.NSMakeRect(24, 198, 512, 20));
    pbar.indeterminate = false;
    pbar.minValue = 0.0;
    pbar.maxValue = 100.0;
    pbar.doubleValue = 0.0;
    content.addSubview(pbar);

    // Live Technical Log ScrollView
    var scrollView = $.NSScrollView.alloc.initWithFrame($.NSMakeRect(24, 64, 512, 124));
    scrollView.hasVerticalScroller = true;
    scrollView.borderType = $.NSBezelBorder;

    var textView = $.NSTextView.alloc.initWithFrame($.NSMakeRect(0, 0, 495, 124));
    textView.editable = false;
    textView.backgroundColor = $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.031, 0.024, 0.059, 1.0);
    textView.textColor = $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.616, 0.580, 0.722, 1.0);
    textView.font = $.NSFont.userFixedPitchFontOfSize(10.5);
    textView.string = isInstall
        ? "Ready to install Ears Audio Captioner.\nClick 'Install Ears Now' below to begin...\n"
        : "Ready to uninstall Ears Audio Captioner.\nClick 'Uninstall Ears' below to begin...\n";
    scrollView.documentView = textView;
    content.addSubview(scrollView);

    // Buttons
    var btnPrimary = $.NSButton.alloc.initWithFrame($.NSMakeRect(256, 16, 184, 34));
    btnPrimary.title = isInstall ? "Install Ears Now" : "Uninstall Ears";
    btnPrimary.bezelStyle = $.NSRoundedBezelStyle;
    content.addSubview(btnPrimary);

    var btnClose = $.NSButton.alloc.initWithFrame($.NSMakeRect(448, 16, 88, 34));
    btnClose.title = "Close";
    btnClose.bezelStyle = $.NSRoundedBezelStyle;
    content.addSubview(btnClose);

    var logFile = isInstall ? "/tmp/ears_macos_install.log" : "/tmp/ears_macos_uninstall.log";
    var statusFile = isInstall ? "/tmp/ears_macos_install.status" : "/tmp/ears_macos_uninstall.status";

    var state = {
        running: false,
        finished: false,
        browserOpened: false
    };

    function stripAnsi(s) {
        return s.replace(/\x1b\[[0-9;]*m/g, "");
    }

    function startWorker() {
        if (state.finished && isInstall) {
            $.system("open 'http://127.0.0.1:8765' >/dev/null 2>&1 &");
            app.stop(null);
            return;
        }
        if (state.running) {
            return;
        }
        state.running = true;
        btnPrimary.enabled = false;
        pbar.doubleValue = 12.0;
        statusLbl.stringValue = isInstall
            ? "[1/4] Auditing macOS prerequisites (Python 3 & FFmpeg)..."
            : "[1/4] Stopping Ears Menu Bar process & removing app bundle...";

        $.system("rm -f '" + logFile + "' '" + statusFile + "'");

        var scriptPath = appDir + (isInstall ? "/packaging/macos/install_macos.sh" : "/packaging/macos/uninstall_macos.sh");
        var extraFlag = "";
        if (!isInstall) {
            extraFlag = (chkPurge && chkPurge.state === $.NSOnState) ? " --purge" : " --keep-data";
        }
        var cmd = "(bash '" + scriptPath + "'" + extraFlag + " > '" + logFile + "' 2>&1; echo $? > '" + statusFile + "') &";
        $.system(cmd);
    }

    win.makeKeyAndOrderFront(null);
    app.activateIgnoringOtherApps(true);

    // Event & Progress Polling Loop
    var runningLoop = true;
    while (runningLoop) {
        var event = app.nextEventMatchingMaskUntilDateInModeDequeue(
            $.NSAnyEventMask,
            $.NSDate.dateWithTimeIntervalSinceNow(0.15),
            $.NSDefaultRunLoopMode,
            true
        );
        if (event) {
            if (event.type === $.NSLeftMouseUp) {
                var loc = event.locationInWindow;
                if ($.NSPointInRect(loc, btnClose.frame)) {
                    runningLoop = false;
                    break;
                }
                if (btnPrimary.enabled && $.NSPointInRect(loc, btnPrimary.frame)) {
                    if (state.finished && !isInstall) {
                        runningLoop = false;
                        break;
                    }
                    startWorker();
                }
            }
            app.sendEvent(event);
            app.updateWindows;
        }

        if (!win.visible) {
            runningLoop = false;
            break;
        }

        if (state.running) {
            var nsLog = $.NSString.stringWithContentsOfFileEncodingError(logFile, $.NSUTF8StringEncoding, null);
            if (nsLog) {
                var raw = stripAnsi(ObjC.unwrap(nsLog));
                if (raw.length > 0) {
                    textView.string = raw;
                    textView.scrollRangeToVisible($.NSMakeRange(raw.length, 0));

                    var lines = raw.split(/\r?\n/);
                    for (var i = 0; i < lines.length; i++) {
                        var ln = lines[i].trim();
                        if (ln.indexOf("[1/4]") === 0) {
                            if (pbar.doubleValue < 25) pbar.doubleValue = 25;
                            statusLbl.stringValue = ln;
                        } else if (ln.indexOf("[2/4]") === 0) {
                            if (pbar.doubleValue < 50) pbar.doubleValue = 50;
                            statusLbl.stringValue = ln;
                        } else if (ln.indexOf("Installing Ears Python dependencies") !== -1) {
                            if (pbar.doubleValue < 68) pbar.doubleValue = 68;
                            statusLbl.stringValue = ln;
                        } else if (ln.indexOf("[3/4]") === 0) {
                            if (pbar.doubleValue < 84) pbar.doubleValue = 84;
                            statusLbl.stringValue = ln;
                        } else if (ln.indexOf("[4/4]") === 0) {
                            if (pbar.doubleValue < 92) pbar.doubleValue = 92;
                            statusLbl.stringValue = ln;
                        }
                    }
                }
            }

            var nsExit = $.NSString.stringWithContentsOfFileEncodingError(statusFile, $.NSUTF8StringEncoding, null);
            if (nsExit) {
                var codeStr = ObjC.unwrap(nsExit).trim();
                if (codeStr.length > 0) {
                    state.running = false;
                    var exitCode = parseInt(codeStr, 10);
                    if (exitCode === 0) {
                        state.finished = true;
                        pbar.doubleValue = 100.0;
                        statusLbl.textColor = $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.204, 0.827, 0.600, 1.0);
                        if (isInstall) {
                            statusLbl.stringValue = "Installation Complete! Web Setup Wizard opened at http://127.0.0.1:8765";
                            btnPrimary.title = "Open Setup Dashboard";
                            btnPrimary.enabled = true;
                            if (!state.browserOpened) {
                                state.browserOpened = true;
                                $.system("open 'http://127.0.0.1:8765' >/dev/null 2>&1 &");
                            }
                        } else {
                            statusLbl.stringValue = "Ears has been cleanly uninstalled from your Mac.";
                            btnPrimary.title = "Done";
                            btnPrimary.enabled = true;
                        }
                    } else {
                        statusLbl.textColor = $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.973, 0.443, 0.443, 1.0);
                        statusLbl.stringValue = "Process exited with code " + exitCode + ". Review the log box below.";
                        btnPrimary.title = "Retry";
                        btnPrimary.enabled = true;
                    }
                }
            }
        }
    }
}
