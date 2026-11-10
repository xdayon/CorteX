import { Component, type ErrorInfo, type ReactNode } from "react";
import { navigate } from "../router";
import { Button, EmptyState } from "../ui";

type Props = { resetKey: string; children: ReactNode };
type State = { error: Error | null; key: string };

/** Keeps the top bar usable when a page throws while rendering; clears itself when the route changes. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, key: this.props.resetKey };

  static getDerivedStateFromError(error: Error): Partial<State> { return { error }; }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    return props.resetKey === state.key ? null : { error: null, key: props.resetKey };
  }

  componentDidCatch(error: Error, info: ErrorInfo) { console.error(error, info.componentStack); }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return <EmptyState title="Algo quebrou nesta tela" action={<>
      <Button onClick={() => window.location.reload()}>Recarregar</Button>
      <Button variant="primary" onClick={() => navigate("/")}>Voltar aos episódios</Button>
    </>}>{error.message}</EmptyState>;
  }
}
