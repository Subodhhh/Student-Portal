import { Outlet } from "react-router-dom";
import "../styles/TestLayout.css";

function TestLayout() {
  return (
    <div className="test-layout">
      <Outlet />
    </div>
  );
}

export default TestLayout;