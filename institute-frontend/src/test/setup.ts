import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// Pages mounted through the real route tree wait for several (stubbed) requests: session, providers, branches, data.
configure({ asyncUtilTimeout: 4000 });

// Tests never reach the network. Each test stubs fetch; vi.unstubAllGlobals() then restores THIS function, so a request
// still in flight from an earlier test cannot reach a real server (e.g. a local gateway answering 401).
globalThis.fetch = () => Promise.reject(new TypeError("Network access is disabled in tests."));

afterEach(() => cleanup());
