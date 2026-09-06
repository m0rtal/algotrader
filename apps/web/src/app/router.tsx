import { Route, Switch } from 'wouter';
import { Dashboard } from '@/pages/Dashboard';

export function Router() {
  return (
    <Switch>
      <Route path="/" component={Dashboard} />
      <Route>
        <Dashboard />
      </Route>
    </Switch>
  );
}
