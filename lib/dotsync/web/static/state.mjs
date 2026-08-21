export const initialState = Object.freeze({
  surface: "manager",
  destination: "overview",
  providers: {},
  accounts: [],
  sync: null,
  jobs: {},
  modal: null,
  error: null,
});


export function reduce(state, event) {
  switch (event.type) {
    case "BOOTSTRAP_LOADED":
      return { ...state, providers: event.providers, error: null };
    case "ACCOUNTS_LOADED":
      return { ...state, accounts: [...event.accounts], error: null };
    case "SYNC_LOADED":
      return { ...state, sync: event.sync, error: null };
    case "JOB_UPDATED":
      return {
        ...state,
        jobs: { ...state.jobs, [event.job.id]: event.job },
      };
    case "NAVIGATED":
      return { ...state, destination: event.destination, modal: null };
    case "ERROR_RAISED":
      return { ...state, error: event.error };
    default:
      return state;
  }
}
