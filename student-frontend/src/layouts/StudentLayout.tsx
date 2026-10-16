import { useState } from "react";
import Sidebar from "../components/Sidebar";
import Header from "../components/Header";
import { Outlet } from "react-router-dom";
import "../styles/student-layout.css";

function StudentLayout() {
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);

  function closeMobileSidebar() {
    setMobileSidebarOpen(false);
  }

  return (
    <div className="student-layout">
      <div
        className={`mobile-sidebar-overlay ${
          mobileSidebarOpen ? "visible" : ""
        }`}
        onClick={closeMobileSidebar}
      />

      <div
        className={`mobile-sidebar-container ${
          mobileSidebarOpen ? "open" : ""
        }`}
      >
        <Sidebar onNavigate={closeMobileSidebar} />
      </div>

      <div className="desktop-sidebar-container">
        <Sidebar />
      </div>

      <div className="student-main">
        <Header onMenuClick={() => setMobileSidebarOpen(true)} />

        <main className="student-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export default StudentLayout;