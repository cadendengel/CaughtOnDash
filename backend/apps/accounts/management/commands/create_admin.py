from django.core.management.base import BaseCommand, CommandError
from apps.accounts.models import AdminUser


class Command(BaseCommand):
    help = 'Create an AdminUser by clerk_user_id or email'

    def add_arguments(self, parser):
        parser.add_argument('--clerk-user-id', dest='clerk_user_id', help='Clerk user id')
        parser.add_argument('--email', dest='email', help='Email address')

    def handle(self, *args, **options):
        clerk_user_id = options.get('clerk_user_id')
        email = options.get('email')

        if not clerk_user_id and not email:
            raise CommandError('Provide --clerk-user-id or --email to create an admin')

        if clerk_user_id and AdminUser.objects.filter(clerk_user_id=clerk_user_id).exists():
            self.stdout.write(self.style.NOTICE('Admin already exists for clerk_user_id'))
            return

        admin = AdminUser.objects.create(clerk_user_id=clerk_user_id or '', email=email or '')
        self.stdout.write(self.style.SUCCESS(f'Created admin: {admin.clerk_user_id or admin.email}'))
