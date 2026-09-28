import StatCard from "../../components/StatCard";
import "../../styles/dashboard.css";

const upcomingTests = [
  {
    name: "NEET Full Mock Test 01",
    date: "25 Sep 2026",
    time: "10:00 AM",
    duration: "180 min",
  },
  {
    name: "Physics Chapter Test",
    date: "27 Sep 2026",
    time: "02:00 PM",
    duration: "60 min",
  },
];

const recentResults = [
  {
    name: "NEET Mock Test 01",
    score: "645 / 720",
    percentage: "89.58%",
  },
  {
    name: "Physics Test 05",
    score: "82 / 100",
    percentage: "82%",
  },
];

function DashboardPage() {
  return (
    <div className="dashboard-page">
      <section className="dashboard-welcome">
        <div>
          <span className="dashboard-label">STUDENT DASHBOARD</span>
          <h2>Welcome back, Student</h2>
          <p>Here is your latest test activity and performance overview.</p>
        </div>

      </section>

      <section className="dashboard-stats">
        <StatCard
          label="Total Tests"
          value={24}
          description="Tests completed"
        />

        <StatCard
          label="Average Score"
          value="84.6%"
          description="Across completed tests"
        />

        <StatCard
          label="Upcoming"
          value={2}
          description="Scheduled tests"
        />

        <StatCard
          label="Current Rank"
          value="#18"
          description="Latest available ranking"
        />
      </section>

      <section className="dashboard-grid">
        <div className="dashboard-panel">
          <div className="dashboard-panel-header">
            <div>
              <h3>Upcoming Tests</h3>
              <p>Your next scheduled tests</p>
            </div>

            <button>View all</button>
          </div>

          <div className="dashboard-list">
            {upcomingTests.map((test) => (
              <div className="dashboard-list-item" key={test.name}>
                <div>
                  <strong>{test.name}</strong>
                  <p>
                    {test.date} · {test.time}
                  </p>
                </div>

                <span>{test.duration}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="dashboard-panel">
          <div className="dashboard-panel-header">
            <div>
              <h3>Recent Results</h3>
              <p>Your latest completed tests</p>
            </div>

            <button>View all</button>
          </div>

          <div className="dashboard-list">
            {recentResults.map((result) => (
              <div className="dashboard-list-item" key={result.name}>
                <div>
                  <strong>{result.name}</strong>
                  <p>{result.score}</p>
                </div>

                <span className="result-percentage">
                  {result.percentage}
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="dashboard-performance">
        <span className="dashboard-label">PERFORMANCE</span>
        <h3>Keep improving your consistency</h3>
        <p>
          Your current average score is 84.6%. Continue taking scheduled tests
          to build your performance history.
        </p>

        <button>View Performance</button>
      </section>
    </div>
  );
}

export default DashboardPage;