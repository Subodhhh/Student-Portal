import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppRoutes } from "../App";
import { AuthProvider } from "../auth/AuthContext";

/** Mounts the real route tree (guards, shell, header) at `path` (with router `state`, as a link may pass), with fetch stubbed by the caller. */
export function renderApp(path: string, state?: unknown) {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[state === undefined ? path : { pathname: path, state }]}>
        <AppRoutes />
      </MemoryRouter>
    </AuthProvider>,
  );
}
