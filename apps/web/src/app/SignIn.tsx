import { signIn } from "../shared/api/client";

/**
 * The sign-in screen (identity spec 4): shown when there is no session or a sign-in failed. The
 * button starts sign-in at the API, which hands over to the identity provider; this page never
 * sees a password or a token.
 */
export function SignIn({ error }: { error: string | null }) {
  return (
    <main className="pw-signin">
      <section className="pw-panel pw-signin-card" aria-labelledby="pw-signin-title">
        <span className="pw-brand-logo pw-signin-logo" role="img" aria-label="PalletWise logo" />
        <h1 id="pw-signin-title">PalletWise</h1>
        <p className="pw-muted">The warehouse copilot for your team.</p>
        {error ? (
          <p className="pw-signin-error" role="alert">
            {error}
          </p>
        ) : (
          <p>Sign in with your organisation account to see your warehouse.</p>
        )}
        <button type="button" className="pw-button pw-signin-button" onClick={signIn}>
          Sign in
        </button>
      </section>
    </main>
  );
}
