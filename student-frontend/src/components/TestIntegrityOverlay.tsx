import { useEffect, useState } from "react";
import { enterFullscreen } from "./examIntegrity";
import type { IntegrityState } from "./examIntegrity";
import "../styles/test-integrity.css"; // We will create this CSS file next

interface Props {
  integrity: IntegrityState | null;
  isFullscreen: boolean;
}

export function TestIntegrityOverlay({ integrity, isFullscreen }: Props) {
  const [showFlash, setShowFlash] = useState(false);
  const [prevWarnings, setPrevWarnings] = useState(0);

  // Trigger a red flash when a warning is recorded
  useEffect(() => {
    if (integrity && integrity.warnings > prevWarnings) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShowFlash(true);
      const timer = setTimeout(() => setShowFlash(false), 1500);
      return () => clearTimeout(timer);
    }
    if (integrity) setPrevWarnings(integrity.warnings);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [integrity?.warnings, prevWarnings]);

  // If the student is not in fullscreen, block the screen with a prompt
  if (!isFullscreen) {
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

  if (!integrity) return null;

  const isDanger = integrity.warnings >= integrity.threshold;
  const isWarning = integrity.warnings > 0 && !isDanger;

  return (
    <>
      {/* Warning Flash Overlay */}
      {showFlash && <div className="integrity-flash" />}

      {/* Persistent Header Badge */}
      <div className={`integrity-badge ${isDanger ? "danger" : isWarning ? "warning" : "safe"}`}>
        <span className="integrity-icon-small">🛡️</span>
        <span>Warnings: {integrity.warnings} / {integrity.threshold}</span>
      </div>
    </>
  );
}