import { Outlet } from "react-router-dom";
import "../styles/test-layout.css";

function TestLayout() {
  return (
    <div className="test-layout">
      <Outlet />
    </div>
  );
}

export default TestLayout;