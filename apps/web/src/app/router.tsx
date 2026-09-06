import { Route, Switch } from 'wouter';
import { Dashboard } from '@pages/Dashboard';
import { Settings } from '@pages/Settings';

export function Router() {
  return (
    <Switch>
      <Route path="/settings" component={Settings} />
      <Route path="/" component={Dashboard} />
      <Route>
        <Dashboard />
      </Route>
    </Switch>
  );
}
