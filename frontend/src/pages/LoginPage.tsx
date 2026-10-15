import { zodResolver } from '@hookform/resolvers/zod';
import { Alert, Button, Center, Paper, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core';
import { useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { Navigate, useNavigate, useSearchParams } from 'react-router';
import { z } from 'zod';

import { ApiError, login } from '../api/client';
import { ME_KEY, safeNext, useMeQuery } from '../auth/auth';
import { BRAND } from '../config';

export const loginSchema = z.object({
  username: z.string().trim().min(1, 'Enter your username'),
  password: z.string().min(1, 'Enter your password'),
});
type LoginValues = z.infer<typeof loginSchema>;

/**
 * Design 4.1: a failed login says only "Invalid credentials" (400). A 403 is
 * a correct password on an account with no role here (api/auth.py): the
 * server's own message is shown. A CSRF refusal is also 403.
 */
export function loginErrorMessage(e: unknown): string {
  if (!(e instanceof ApiError)) return 'The server could not be reached. Please try again.';
  if (e.status === 400 || e.status === 401) return 'Invalid credentials';
  if (e.status === 403) {
    return /csrf/i.test(e.detail)
      ? 'The sign-in could not be verified. Reload the page and try again.'
      : e.detail;
  }
  return 'Sign-in failed. Please try again.';
}

export function LoginForm({ onSuccess }: { onSuccess: () => void }) {
  const qc = useQueryClient();
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { username: '', password: '' },
  });

  const onSubmit = handleSubmit(async ({ username, password }) => {
    try {
      const me = await login(username, password);
      qc.clear();
      qc.setQueryData(ME_KEY, me);
      onSuccess();
    } catch (e) {
      setError('root', { message: loginErrorMessage(e) });
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate>
      <Stack>
        {errors.root?.message && (
          <Alert color="red" role="alert" title="Sign-in failed">
            {errors.root.message}
          </Alert>
        )}
        <TextInput
          label="Username"
          autoComplete="username"
          required
          {...register('username')}
          error={errors.username?.message}
        />
        <PasswordInput
          label="Password"
          autoComplete="current-password"
          required
          {...register('password')}
          error={errors.password?.message}
        />
        <Button type="submit" loading={isSubmitting}>
          Sign in
        </Button>
      </Stack>
    </form>
  );
}

export function LoginPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { data: me } = useMeQuery();
  const next = safeNext(params.get('next'));
  const expired = params.get('expired') === '1';

  if (me) return <Navigate to={next} replace />;

  return (
    <Center mih="100vh" p="md" component="main">
      <Paper withBorder shadow="sm" p="xl" w={380}>
        <Stack gap="xs" mb="md">
          <Text size="sm" c="dimmed">
            {BRAND.logoPlaceholder}
          </Text>
          <Title order={1} size="h3">
            {BRAND.bankName}
          </Title>
          <Text c="dimmed">{BRAND.appName}</Text>
        </Stack>
        {expired && (
          <Alert color="yellow" mb="md" role="status" title="Session expired">
            Your session ended after 15 minutes without activity. Sign in again to continue where you were.
          </Alert>
        )}
        <LoginForm onSuccess={() => navigate(next, { replace: true })} />
      </Paper>
    </Center>
  );
}
