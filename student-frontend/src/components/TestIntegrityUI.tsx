import { useEffect, useState } from "react";
import type {  IntegrityState } from "./examIntegrity";
import { enterFullscreen } from "./examIntegrity";
import "../styles/test-integrity.css";

// 1. The Rules Screen
export function ExamRulesScreen({ onAccept }: { onAccept: () => void }) {
  return (
    <div className="exam-rules-wrapper">
      <div className="exam-rules-card">
        <div className="exam-rules-header">
          <h2>Exam Integrity Agreement</h2>
          <p>Please read carefully before starting.</p>
        </div>
        
        <div className="exam-rules-list">
          <div className="rule-item"><span>🛡️</span> <div><strong>Fullscreen Mode</strong><p>The test opens in fullscreen. Do not exit.</p></div></div>
          <div className="rule-item"><span>🧩</span> <div><strong>Disable Extensions</strong><p>Please turn off all browser extensions before starting (they can trigger security warnings).</p></div></div>
          <div className="rule-item"><span>🚫</span> <div><strong>No Copy/Paste/Right-Click</strong><p>Copying, pasting, cutting, and the right-click menu are disabled.</p></div></div>
          <div className="rule-item"><span>⌨️</span> <div><strong>Shortcuts Blocked</strong><p>Keyboard shortcuts (Ctrl+C, Ctrl+V, F12, Inspect) are disabled.</p></div></div>
          <div className="rule-item"><span>⚠️</span> <div><strong>Warnings</strong><p>Breaking a rule records a warning. At 3 warnings, the test auto-submits.</p></div></div>
        </div>

        <button type="button" className="exam-start-btn" onClick={() => {
          enterFullscreen();
          onAccept();
        }}>
          I Understand, Start Exam
        </button>
      </div>
    </div>
  );
}

// 2. Fullscreen Blocker
export function FullscreenBlocker({ isFullscreen }: { isFullscreen: boolean }) {
  if (isFullscreen) return null;

  return (
    <div className="integrity-fullscreen-blocker">
      <div className="integrity-blocker-content">
        <div className="integrity-icon">⚠️</div>
        <h2>Fullscreen Required</h2>
        <p>You have left fullscreen mode. Please return to fullscreen immediately to continue your exam.</p>
        <button type="button" onClick={enterFullscreen} className="integrity-btn-primary">
          Return to Fullscreen
        </button>
      </div>
    </div>
  );
}

// 3. The Warning Badge + Center/Top Banner
export function IntegrityBadge({ integrity }: { integrity: IntegrityState | null }) {
  const [showFlash, setShowFlash] = useState(false);
  const [showBanner, setShowBanner] = useState(false);
  
  const currentWarnings = integrity?.warnings ?? 0;
  const [prevWarnings, setPrevWarnings] = useState(currentWarnings);

  useEffect(() => {
    if (currentWarnings > prevWarnings) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShowFlash(true);
      setShowBanner(true);
      
      const flashTimer = setTimeout(() => setShowFlash(false), 1500);
      const bannerTimer = setTimeout(() => setShowBanner(false), 4000); // Banner stays for 4 seconds
      
      setPrevWarnings(currentWarnings);
      return () => {
        clearTimeout(flashTimer);
        clearTimeout(bannerTimer);
      };
    } else if (currentWarnings < prevWarnings) {
      setPrevWarnings(currentWarnings);
    }
  }, [currentWarnings, prevWarnings]);

  if (!integrity) return null;

  const isDanger = integrity.warnings >= integrity.threshold;
  const isWarning = integrity.warnings > 0 && !isDanger;

  return (
    <>
      {/* Red Flash */}
      {showFlash && <div className="integrity-flash" />}

      {/* Center/Top Warning Banner */}
      {showBanner && (
        <div className="integrity-top-banner">
          <span className="banner-icon">⚠️</span>
          <div>
            <strong>Security Warning</strong>
            <p>Action blocked. This attempt has been recorded on the server.</p>
          </div>
        </div>
      )}

      {/* Small Top-Right Badge */}
      <div className={`integrity-badge ${isDanger ? "danger" : isWarning ? "warning" : "safe"}`}>
        <span className="integrity-icon-small">🛡️</span>
        <span>Warnings: {integrity.warnings} / {integrity.threshold}</span>
      </div>
    </>
  );
}