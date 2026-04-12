from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.text import slugify

class Company(models.Model):
    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=220, unique=True, blank=True)

    class Meta:
        verbose_name = 'Company'
        verbose_name_plural = 'Companies'

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

class Department(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='departments')
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, blank=True)

    class Meta:
        unique_together = ('company', 'name')
        verbose_name = 'Department'
        verbose_name_plural = 'Departments'

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.company.name} / {self.name}'

class Unit(models.Model):
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='units')
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, blank=True)

    class Meta:
        unique_together = ('department', 'name')
        verbose_name = 'Unit'
        verbose_name_plural = 'Units'

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.department.name} / {self.name}'

class User(AbstractUser):
    ROLE_ADMIN = 'admin'
    ROLE_MANAGER = 'manager'
    ROLE_STAFF = 'staff'
    ROLE_CHOICES = [
        (ROLE_ADMIN, 'Admin'),
        (ROLE_MANAGER, 'Manager'),
        (ROLE_STAFF, 'Staff'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_STAFF)
    company = models.ForeignKey(Company, on_delete=models.SET_NULL, null=True, blank=True, related_name='users')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='users')
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True, related_name='users')

    def is_admin(self):
        return self.role == self.ROLE_ADMIN or self.is_superuser

    def is_manager(self):
        return self.role == self.ROLE_MANAGER

    def is_staff_role(self):
        return self.role == self.ROLE_STAFF

    def __str__(self):
        return self.get_full_name() or self.username
